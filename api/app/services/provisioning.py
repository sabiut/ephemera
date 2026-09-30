"""
Shared entry point for "a PR wants a preview environment".

Both the GitHub webhook handler and the REST API call this, so the rules for
reusing a record after a PR is reopened live in one place.
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud import deployment as deployment_crud
from app.crud import environment as environment_crud
from app.config import settings
from app.models import Environment, EnvironmentStatus, User
from app.services.github import github_service

logger = logging.getLogger(__name__)

Action = Literal["exists", "created", "reprovisioned"]

# Statuses in which a preview still holds cluster resources (a namespace,
# possibly crash-looping pods), and so counts against the repository's limit.
HOLDS_RESOURCES = (
    EnvironmentStatus.PENDING, EnvironmentStatus.PROVISIONING, EnvironmentStatus.READY,
    EnvironmentStatus.UPDATING, EnvironmentStatus.FAILED,
)


class PreviewLimitReached(Exception):
    """The repository already has PREVIEW_MAX_ACTIVE_PER_REPOSITORY previews."""

    def __init__(self, limit: int, pr_numbers: List[int]):
        self.limit = limit
        self.pr_numbers = pr_numbers
        prs = ", ".join(f"#{n}" for n in pr_numbers)
        super().__init__(
            f"This repository already has {len(pr_numbers)} previews (the limit is {limit}): {prs}. "
            "Stop one you don't need right now (Ephemera dashboard: Details, then Stop preview), "
            "close one of those pull requests, or wait for an idle one to expire. Then push a commit "
            "or retry the preview."
        )


def _admission_lock(db: Session, repository_full_name: str) -> None:
    """
    Take a transaction-scoped Postgres advisory lock for the repository. It
    is held until this request commits its new or reset environment (the
    CRUD functions commit), so the next request's limit check sees that row.
    SQLite, used by the tests, allows one writer at a time and has no such
    lock.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    key = int.from_bytes(hashlib.sha256(repository_full_name.lower().encode()).digest()[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def repository_usage(db: Session, repository_full_name: str) -> "tuple[List[int], int]":
    """(PR numbers holding a preview, the limit; 0 means unlimited) for a repository."""
    rows = db.query(Environment.pr_number).filter(
        environment_crud.same_repository(Environment.repository_full_name, repository_full_name),
        Environment.status.in_(HOLDS_RESOURCES),
    ).order_by(Environment.pr_number).all()
    return [row[0] for row in rows], settings.preview_max_active_per_repository


def _check_limit(db: Session, req: "EnvironmentRequest") -> None:
    limit = settings.preview_max_active_per_repository
    if limit <= 0:
        return
    holding = db.query(Environment.pr_number).filter(
        environment_crud.same_repository(Environment.repository_full_name, req.repository_full_name),
        Environment.pr_number != req.pr_number,
        Environment.status.in_(HOLDS_RESOURCES),
    ).order_by(Environment.pr_number).all()
    if len(holding) >= limit:
        raise PreviewLimitReached(limit, [row[0] for row in holding])


@dataclass
class EnvironmentRequest:
    repository_full_name: str
    repository_name: str
    pr_number: int
    pr_title: Optional[str]
    branch_name: str
    commit_sha: str
    installation_id: int
    owner: User


def request_environment(db: Session, req: EnvironmentRequest) -> Tuple[Environment, Action]:
    """
    Ensure an environment exists and is being provisioned for the PR.

    - No record: create one and queue provisioning ("created").
    - Record still live (pending/provisioning/ready/updating): leave it alone ("exists").
    - Record destroyed or failed (e.g. PR reopened): reset it and queue provisioning
      again ("reprovisioned"). The namespace name is derived from the PR, so the
      old record is reused rather than duplicated.
    """
    from app.tasks.environment import provision_environment  # avoid import cycle

    # Serialize admission per repository: the limit check and the insert
    # must not interleave with another request's, or two requests can both
    # see a free slot. Also stops two requests for one PR racing to insert.
    _admission_lock(db, req.repository_full_name)
    existing = environment_crud.get_environment_by_pr(db, req.repository_full_name, req.pr_number)

    if existing and (existing.is_active or existing.status == EnvironmentStatus.PENDING):
        if existing.closed_at is not None:
            # Reopened before the teardown ran: it will see this and stand down.
            environment_crud.mark_reopened(db, existing)
        logger.info(f"Environment {existing.namespace} already live for PR #{req.pr_number}")
        return existing, "exists"

    # A new or re-created preview must fit the repository's limit; an
    # existing live one (above) never counts against itself.
    _check_limit(db, req)

    env_url = github_service.build_environment_url(
        req.pr_number, req.repository_full_name, namespace=existing.namespace if existing else None)

    if existing:
        environment = environment_crud.reset_environment(
            db,
            existing,
            pr_title=req.pr_title,
            branch_name=req.branch_name,
            commit_sha=req.commit_sha,
            installation_id=req.installation_id,
            environment_url=env_url,
        )
        action: Action = "reprovisioned"
        logger.info(f"Re-provisioning environment {environment.namespace} for PR #{req.pr_number}")
    else:
        environment = environment_crud.create_environment(
            db=db,
            repository_full_name=req.repository_full_name,
            repository_name=req.repository_name,
            pr_number=req.pr_number,
            pr_title=req.pr_title,
            branch_name=req.branch_name,
            commit_sha=req.commit_sha,
            installation_id=req.installation_id,
            owner=req.owner,
            environment_url=env_url,
        )
        action = "created"
        logger.info(f"Created environment {environment.namespace} for PR #{req.pr_number}")

    deployment = deployment_crud.create_deployment(db, environment, req.commit_sha)
    logger.info(f"Created deployment {deployment.id} for environment {environment.id}")

    provision_environment.delay(
        environment_id=environment.id,
        installation_id=req.installation_id,
        repo_full_name=req.repository_full_name,
        pr_number=req.pr_number,
        commit_sha=req.commit_sha,
        deployment_id=deployment.id,
    )

    return environment, action
