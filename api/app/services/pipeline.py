"""
Previews created by a CI/CD pipeline (docs/ci-pipeline.md).

A pipeline that has built its images asks for a preview by repository and
name; the same request again updates it. From there the preview is an
ordinary environment: the same namespace setup, isolation, registry tokens,
protection, readiness checks, diagnosis and cleanup as a pull request's.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.crud import deployment as deployment_crud
from app.crud import environment as environment_crud
from app.models import Environment, EnvironmentStatus, User
from app.services.provisioning import _admission_lock, check_limit

logger = logging.getLogger(__name__)

NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,18}[a-z0-9])?$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
# registry/path/name[:tag][@sha256:digest], as docker accepts it (loosely).
IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:-]*(@sha256:[0-9a-f]{64})?$", re.I)
SERVICE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


class InvalidRequest(ValueError):
    pass


class OutOfOrder(Exception):
    """A request older (by serial) than the run that last deployed the preview."""


@dataclass
class PipelineRequest:
    repository_full_name: str
    name: str
    commit_sha: str
    installation_id: int
    owner: User
    images: Dict[str, str] = field(default_factory=dict)
    compose: Optional[str] = None
    ttl_hours: int = 0
    pull_request: Optional[int] = None
    serial: Optional[int] = None


def validate(req: PipelineRequest) -> List[str]:
    """Raise InvalidRequest for what can't work; return warnings for what works but shouldn't."""
    if not NAME.match(req.name or ""):
        raise InvalidRequest("name must be 1-20 characters: lowercase letters, digits and '-', "
                             "starting and ending with a letter or digit (for example pr-142)")
    if not COMMIT.match(req.commit_sha or ""):
        raise InvalidRequest("commit must be the full 40-character commit SHA")
    if not 1 <= req.ttl_hours <= settings.pipeline_preview_max_ttl_hours:
        raise InvalidRequest(f"ttl_hours must be between 1 and {settings.pipeline_preview_max_ttl_hours}")
    if req.compose is not None and len(req.compose) > 200_000:
        raise InvalidRequest("compose is larger than 200 KB")
    warnings = []
    for service, image in req.images.items():
        if not SERVICE.match(service):
            raise InvalidRequest(f"{service!r} is not a compose service name")
        if not IMAGE.match(image or "") or len(image) > 500:
            raise InvalidRequest(f"the image for {service} ({image!r}) is not an image reference")
        if "@sha256:" not in image:
            warnings.append(f"{service}: {image} is a tag, not a digest; if the tag moves, the preview may "
                            "not run the image this pipeline built (use image@sha256:…)")
    return warnings


def find(db: Session, repository_full_name: str, name: str) -> Optional[Environment]:
    return db.query(Environment).filter(
        environment_crud.same_repository(Environment.repository_full_name, repository_full_name),
        Environment.preview_name == name,
    ).first()


def request_preview(db: Session, req: PipelineRequest) -> Tuple[Environment, str]:
    """
    Create the named preview, or update it: ("created" | "updated" |
    "reprovisioned"). Raises OutOfOrder for an older run, and
    PreviewLimitReached when the repository has no room for a new one.
    """
    from app.tasks.environment import provision_environment, update_environment  # avoid import cycle

    _admission_lock(db, req.repository_full_name)
    existing = find(db, req.repository_full_name, req.name)
    if existing is not None and req.serial is not None and existing.serial is not None \
            and req.serial < existing.serial:
        db.rollback()
        raise OutOfOrder(f"a newer run (serial {existing.serial}) already deployed this preview")

    expires = datetime.now(timezone.utc) + timedelta(hours=req.ttl_hours)
    live = existing is not None and (existing.is_active or existing.status == EnvironmentStatus.PENDING)
    if not live:
        check_limit(db, req.repository_full_name, existing.id if existing is not None else None)

    if existing is None:
        env = Environment(
            source="pipeline", preview_name=req.name, repository_full_name=req.repository_full_name,
            repository_name=req.repository_full_name.rsplit("/", 1)[-1], pr_number=None,
            pr_title=f"Pipeline preview {req.name}", branch_name=req.name, commit_sha=req.commit_sha,
            installation_id=req.installation_id, owner_id=req.owner.id, status=EnvironmentStatus.PENDING,
        )
        env.namespace = env.generate_namespace()
        action = "created"
        db.add(env)
    else:
        env = existing
        action = "updated" if live else "reprovisioned"
        env.commit_sha = req.commit_sha
        env.installation_id = req.installation_id
        if not live:
            env.status = EnvironmentStatus.PENDING
            env.error_message = None
            env.destroyed_at = None
            env.closed_at = None
            env.removal_reason = None
        else:
            env.status = EnvironmentStatus.UPDATING
    env.image_overrides = dict(req.images) or None
    env.compose_override = req.compose
    env.linked_pr_number = req.pull_request
    env.serial = req.serial if req.serial is not None else env.serial
    env.pipeline_expires_at = expires
    env.kept_at = None
    environment_crud._queue(env)
    db.commit()
    db.refresh(env)
    if env.environment_url is None:
        env.environment_url = f"https://{env.namespace}.{settings.base_domain}"  # until services are known
        db.commit()

    deployment = deployment_crud.create_deployment(db, env, req.commit_sha)
    task = update_environment if action == "updated" else provision_environment
    task.delay(environment_id=env.id, installation_id=req.installation_id, repo_full_name=req.repository_full_name,
               pr_number=req.pull_request, commit_sha=req.commit_sha, deployment_id=deployment.id)
    logger.info(f"Pipeline preview {req.repository_full_name}:{req.name} {action} at {req.commit_sha[:7]}")
    return env, action
