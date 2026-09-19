"""
Shared entry point for "a PR wants a preview environment".

Both the GitHub webhook handler and the REST API call this, so the rules for
reusing a record after a PR is reopened live in one place.
"""

import logging
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

from sqlalchemy.orm import Session

from app.crud import deployment as deployment_crud
from app.crud import environment as environment_crud
from app.models import Environment, EnvironmentStatus, User
from app.services.github import github_service

logger = logging.getLogger(__name__)

Action = Literal["exists", "created", "reprovisioned"]


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

    existing = environment_crud.get_environment_by_pr(db, req.repository_full_name, req.pr_number)

    if existing and (existing.is_active or existing.status == EnvironmentStatus.PENDING):
        logger.info(f"Environment {existing.namespace} already live for PR #{req.pr_number}")
        return existing, "exists"

    env_url = github_service.build_environment_url(req.pr_number, req.repository_name)

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
    )

    return environment, action
