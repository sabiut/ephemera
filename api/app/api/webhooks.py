import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import ValidationError

from app.core.security import verify_github_delivery, verify_github_webhook
from app.crud import deployment as deployment_crud
from app.crud import environment as environment_crud
from app.crud import user as user_crud
from app.database import SessionLocal
from app.models.environment import EnvironmentStatus
from app.schemas.github import PullRequestWebhook
from app.services.github import github_service
from app.services.provisioning import EnvironmentRequest, request_environment
from app.tasks.environment import destroy_environment, update_environment

logger = logging.getLogger(__name__)
router = APIRouter()

# Handlers below are plain (sync) functions on purpose: FastAPI runs them in a
# threadpool after the response is sent, so the blocking PyGithub calls do not
# stall the event loop. Each opens its own DB session because the request's
# session is closed before background tasks run.


def handle_pull_request_opened(payload: PullRequestWebhook):
    """Handle PR opened/reopened: create or re-provision the environment."""
    pr = payload.pull_request
    repo = payload.repository
    installation_id = github_service.get_installation_id_from_payload(payload.model_dump())

    logger.info(f"PR #{pr.number} {payload.action} in {repo.full_name}")

    if not installation_id:
        logger.error(f"Webhook for {repo.full_name}#{pr.number} has no installation id; ignoring")
        return

    with SessionLocal() as db:
        owner = user_crud.get_or_create_user(
            db=db,
            github_id=pr.user.id,
            github_login=pr.user.login,
            avatar_url=pr.user.avatar_url,
        )

        environment, action = request_environment(
            db,
            EnvironmentRequest(
                repository_full_name=repo.full_name,
                repository_name=repo.name,
                pr_number=pr.number,
                pr_title=pr.title,
                branch_name=pr.head["ref"],
                commit_sha=pr.head["sha"],
                installation_id=installation_id,
                owner=owner,
            ),
        )

        if action == "exists":
            return

        namespace = environment.namespace
        env_url = environment.environment_url

    comment = f"""## Ephemera Environment

Your preview environment is being {"re-created" if action == "reprovisioned" else "created"}...

**Namespace**: `{namespace}`
**Status**: Provisioning

This usually takes 1-2 minutes. You'll receive another comment when it's ready!

---
*Powered by Ephemera*
"""
    github_service.post_comment_to_pr(installation_id, repo.full_name, pr.number, comment)
    github_service.update_pr_status(
        installation_id=installation_id,
        repo_full_name=repo.full_name,
        commit_sha=pr.head["sha"],
        state="pending",
        description="Creating preview environment...",
        target_url=env_url,
    )


def handle_pull_request_closed(payload: PullRequestWebhook):
    """Handle PR closed event - destroy the environment"""
    pr = payload.pull_request
    repo = payload.repository
    installation_id = github_service.get_installation_id_from_payload(payload.model_dump())

    logger.info(f"PR #{pr.number} closed in {repo.full_name}")

    with SessionLocal() as db:
        environment = environment_crud.get_environment_by_pr(db, repo.full_name, pr.number)
        if not environment:
            logger.warning(f"No environment found for PR #{pr.number}, skipping cleanup")
            return
        if not environment.is_active and environment.status != EnvironmentStatus.PENDING:
            logger.info(f"Environment {environment.namespace} already {environment.status.value}, skipping")
            return
        environment_id = environment.id
        namespace = environment.namespace

    destroy_environment.delay(
        environment_id=environment_id,
        installation_id=installation_id,
        repo_full_name=repo.full_name,
        pr_number=pr.number,
        pr_merged=bool(pr.merged),
    )

    if installation_id:
        action = "merged" if pr.merged else "closed"
        comment = f"""## Environment Cleanup

PR was {action}. Preview environment is being destroyed.

**Namespace**: `{namespace}`
**Status**: Destroying

All resources will be cleaned up within 1-2 minutes.

---
*Powered by Ephemera*
"""
        github_service.post_comment_to_pr(installation_id, repo.full_name, pr.number, comment)


def handle_pull_request_synchronize(payload: PullRequestWebhook):
    """Handle PR synchronize event (new commits pushed) - redeploy the environment"""
    pr = payload.pull_request
    repo = payload.repository
    installation_id = github_service.get_installation_id_from_payload(payload.model_dump())
    commit_sha = pr.head["sha"]

    logger.info(f"PR #{pr.number} updated with new commits in {repo.full_name}")

    with SessionLocal() as db:
        environment = environment_crud.get_environment_by_pr(db, repo.full_name, pr.number)
        if not environment:
            logger.warning(f"No environment found for PR #{pr.number}, cannot update")
            return
        if not environment.is_active:
            logger.info(f"Environment {environment.namespace} is {environment.status.value}; not updating")
            return

        environment_crud.update_environment_commit(db, environment, commit_sha)
        deployment = deployment_crud.create_deployment(db, environment, commit_sha)
        logger.info(f"Created deployment {deployment.id} for updated PR #{pr.number}")
        environment_id = environment.id
        namespace = environment.namespace
        env_url = environment.environment_url

    update_environment.delay(
        environment_id=environment_id,
        commit_sha=commit_sha,
        installation_id=installation_id,
        repo_full_name=repo.full_name,
        pr_number=pr.number,
    )

    if installation_id:
        github_service.update_pr_status(
            installation_id=installation_id,
            repo_full_name=repo.full_name,
            commit_sha=commit_sha,
            state="pending",
            description="Updating preview environment...",
            target_url=env_url,
        )

    logger.info(f"Environment {namespace} queued for update")


PR_HANDLERS = {
    "opened": handle_pull_request_opened,
    "reopened": handle_pull_request_opened,
    "closed": handle_pull_request_closed,
    "synchronize": handle_pull_request_synchronize,
}


@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: Optional[str] = Header(None),
):
    """
    Handle GitHub webhook events.

    Verifies the HMAC signature, then dispatches pull_request actions to
    background handlers so GitHub gets a fast acknowledgement.
    """
    body = await verify_github_webhook(request)
    delivery_id = verify_github_delivery(request)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in webhook payload: {delivery_id}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"Received GitHub webhook: {x_github_event} (delivery: {delivery_id})")

    if x_github_event == "ping":
        return {"status": "pong"}

    if x_github_event != "pull_request":
        logger.info(f"Ignoring event type: {x_github_event}")
        return {"status": "ignored", "event": x_github_event}

    try:
        pr_webhook = PullRequestWebhook(**payload)
    except ValidationError as e:
        logger.error(f"Failed to parse PR webhook: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload structure")

    handler = PR_HANDLERS.get(pr_webhook.action)
    if handler:
        background_tasks.add_task(handler, pr_webhook)
    else:
        logger.info(f"Ignoring PR action: {pr_webhook.action}")

    return {
        "status": "received" if handler else "ignored",
        "event": x_github_event,
        "action": pr_webhook.action,
        "pr": pr_webhook.number,
        "delivery_id": delivery_id,
    }
