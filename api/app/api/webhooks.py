import json
import logging
from fastapi import APIRouter, Request, BackgroundTasks, Header, Depends
from typing import Optional
from sqlalchemy.orm import Session

from app.core.security import verify_github_webhook, verify_github_delivery
from app.schemas.github import PullRequestWebhook
from app.services.github import github_service
from app.services.kubernetes import kubernetes_service
from app.database import get_db
from app.crud import user as user_crud
from app.crud import environment as environment_crud
from app.crud import deployment as deployment_crud
from app.models.environment import EnvironmentStatus
from app.models.deployment import DeploymentStatus

logger = logging.getLogger(__name__)
router = APIRouter()


async def handle_pull_request_opened(payload: PullRequestWebhook, db: Session):
    """Handle PR opened event - create a new environment"""
    pr = payload.pull_request
    repo = payload.repository
    installation_id = github_service.get_installation_id_from_payload(payload.dict())

    logger.info(f"PR #{pr.number} opened in {repo.full_name}")

    # Check if environment already exists
    existing_env = environment_crud.get_environment_by_pr(db, repo.full_name, pr.number)
    if existing_env:
        logger.info(f"Environment already exists for PR #{pr.number}, skipping creation")
        return

    # Get or create user
    owner = user_crud.get_or_create_user(
        db=db,
        github_id=pr.user.id,
        github_login=pr.user.login,
        avatar_url=pr.user.avatar_url
    )

    # Build environment URL
    env_url = github_service.build_environment_url(pr.number, repo.name)

    # Create environment in database
    environment = environment_crud.create_environment(
        db=db,
        repository_full_name=repo.full_name,
        repository_name=repo.name,
        pr_number=pr.number,
        pr_title=pr.title,
        branch_name=pr.head["ref"],
        commit_sha=pr.head["sha"],
        installation_id=installation_id,
        owner=owner,
        environment_url=env_url
    )

    logger.info(f"Created environment {environment.namespace} for PR #{pr.number}")

    # Create initial deployment
    deployment = deployment_crud.create_deployment(
        db=db,
        environment=environment,
        commit_sha=pr.head["sha"]
    )

    logger.info(f"Created deployment {deployment.id} for environment {environment.id}")

    # Create Kubernetes namespace
    k8s_labels = {
        "app": "ephemera",
        "pr-number": str(pr.number),
        "repository": repo.name,
        "environment-id": str(environment.id)
    }

    k8s_success = kubernetes_service.create_namespace(
        namespace=environment.namespace,
        labels=k8s_labels
    )

    if k8s_success:
        # Create resource quota
        kubernetes_service.create_resource_quota(
            namespace=environment.namespace,
            cpu_limit="1",
            memory_limit="2Gi",
            pod_limit="10"
        )

        # Update environment status
        environment_crud.update_environment_status(
            db=db,
            environment=environment,
            status=EnvironmentStatus.READY
        )
        logger.info(f"Kubernetes namespace {environment.namespace} created successfully")

        # Post success comment to PR
        if installation_id:
            comment = f"""## Ephemera Environment Ready

Your preview environment has been created!

**Environment URL**: {env_url}
**Namespace**: `{environment.namespace}`
**Status**: Ready

---
*Powered by Ephemera*
"""
            github_service.post_comment_to_pr(
                installation_id=installation_id,
                repo_full_name=repo.full_name,
                pr_number=pr.number,
                comment=comment
            )

            # Update commit status to success
            github_service.update_pr_status(
                installation_id=installation_id,
                repo_full_name=repo.full_name,
                commit_sha=pr.head["sha"],
                state="success",
                description="Preview environment ready",
                context="ephemera/environment",
                target_url=env_url
            )
    else:
        # Update environment status to failed
        environment_crud.update_environment_status(
            db=db,
            environment=environment,
            status=EnvironmentStatus.FAILED,
            error_message="Failed to create Kubernetes namespace"
        )
        logger.error(f"Failed to create Kubernetes namespace {environment.namespace}")

        # Post failure comment to PR
        if installation_id:
            comment = f"""## Ephemera Environment Failed

Failed to create preview environment.

**Namespace**: `{environment.namespace}`
**Status**: Failed

Please check logs or contact support.

---
*Powered by Ephemera*
"""
            github_service.post_comment_to_pr(
                installation_id=installation_id,
                repo_full_name=repo.full_name,
                pr_number=pr.number,
                comment=comment
            )

            # Update commit status to failure
            github_service.update_pr_status(
                installation_id=installation_id,
                repo_full_name=repo.full_name,
                commit_sha=pr.head["sha"],
                state="failure",
                description="Failed to create environment",
                context="ephemera/environment"
            )


async def handle_pull_request_closed(payload: PullRequestWebhook, db: Session):
    """Handle PR closed event - destroy the environment"""
    pr = payload.pull_request
    repo = payload.repository
    installation_id = github_service.get_installation_id_from_payload(payload.dict())

    logger.info(f"PR #{pr.number} closed in {repo.full_name}")

    # Get environment from database
    environment = environment_crud.get_environment_by_pr(db, repo.full_name, pr.number)
    if not environment:
        logger.warning(f"No environment found for PR #{pr.number}, skipping cleanup")
        return

    # Update environment status to destroying
    environment_crud.update_environment_status(
        db=db,
        environment=environment,
        status=EnvironmentStatus.DESTROYING
    )

    logger.info(f"Marked environment {environment.namespace} for destruction")

    # Delete Kubernetes namespace
    k8s_success = kubernetes_service.delete_namespace(environment.namespace)

    if k8s_success:
        # Update environment status to destroyed
        environment_crud.update_environment_status(
            db=db,
            environment=environment,
            status=EnvironmentStatus.DESTROYED
        )
        logger.info(f"Kubernetes namespace {environment.namespace} deleted successfully")

        # Post cleanup success comment
        if installation_id:
            action = "merged" if pr.merged else "closed"

            comment = f"""## Environment Cleanup Complete

PR was {action}. Preview environment has been destroyed.

**Namespace**: `{environment.namespace}`
**Status**: Destroyed

All resources have been cleaned up.

---
*Powered by Ephemera*
"""
            github_service.post_comment_to_pr(
                installation_id=installation_id,
                repo_full_name=repo.full_name,
                pr_number=pr.number,
                comment=comment
            )
    else:
        # Update environment status to failed
        environment_crud.update_environment_status(
            db=db,
            environment=environment,
            status=EnvironmentStatus.FAILED,
            error_message="Failed to delete Kubernetes namespace"
        )
        logger.error(f"Failed to delete Kubernetes namespace {environment.namespace}")


async def handle_pull_request_synchronize(payload: PullRequestWebhook, db: Session):
    """Handle PR synchronize event (new commits pushed) - update environment"""
    pr = payload.pull_request
    repo = payload.repository
    installation_id = github_service.get_installation_id_from_payload(payload.dict())

    logger.info(f"PR #{pr.number} updated with new commits in {repo.full_name}")

    # Get environment from database
    environment = environment_crud.get_environment_by_pr(db, repo.full_name, pr.number)
    if not environment:
        logger.warning(f"No environment found for PR #{pr.number}, cannot update")
        return

    # Update environment with new commit
    environment_crud.update_environment_commit(db, environment, pr.head["sha"])

    # Create new deployment
    deployment = deployment_crud.create_deployment(
        db=db,
        environment=environment,
        commit_sha=pr.head["sha"]
    )

    logger.info(f"Created deployment {deployment.id} for updated PR #{pr.number}")

    # Verify namespace still exists
    namespace_exists = kubernetes_service.namespace_exists(environment.namespace)

    if namespace_exists:
        # Update environment status back to ready
        environment_crud.update_environment_status(
            db=db,
            environment=environment,
            status=EnvironmentStatus.READY
        )

        # Update commit status to success
        if installation_id:
            env_url = github_service.build_environment_url(pr.number, repo.name)
            github_service.update_pr_status(
                installation_id=installation_id,
                repo_full_name=repo.full_name,
                commit_sha=pr.head["sha"],
                state="success",
                description="Environment ready for new commits",
                context="ephemera/environment",
                target_url=env_url
            )

        logger.info(f"Environment {environment.namespace} ready for new deployment")
    else:
        # Namespace was deleted, mark as failed
        environment_crud.update_environment_status(
            db=db,
            environment=environment,
            status=EnvironmentStatus.FAILED,
            error_message="Namespace no longer exists"
        )

        if installation_id:
            github_service.update_pr_status(
                installation_id=installation_id,
                repo_full_name=repo.full_name,
                commit_sha=pr.head["sha"],
                state="failure",
                description="Environment namespace not found",
                context="ephemera/environment"
            )

        logger.error(f"Namespace {environment.namespace} not found for update")


async def handle_pull_request_reopened(payload: PullRequestWebhook, db: Session):
    """Handle PR reopened event - recreate environment"""
    pr = payload.pull_request
    repo = payload.repository

    logger.info(f"PR #{pr.number} reopened in {repo.full_name}")

    # Treat reopened as a new PR
    await handle_pull_request_opened(payload, db)


@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_github_event: Optional[str] = Header(None),
    x_github_delivery: Optional[str] = Header(None)
):
    """
    Handle GitHub webhook events.

    This endpoint receives webhook events from GitHub and processes them accordingly.
    """
    # Verify webhook signature
    body = await verify_github_webhook(request)
    delivery_id = verify_github_delivery(request)

    # Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in webhook payload: {delivery_id}")
        return {"error": "Invalid JSON"}

    logger.info(f"Received GitHub webhook: {x_github_event} (delivery: {delivery_id})")

    # Handle different event types
    if x_github_event == "pull_request":
        # Parse PR webhook
        try:
            pr_webhook = PullRequestWebhook(**payload)
        except Exception as e:
            logger.error(f"Failed to parse PR webhook: {str(e)}")
            return {"error": "Invalid payload structure"}

        # Handle different PR actions
        action = pr_webhook.action

        if action == "opened":
            background_tasks.add_task(handle_pull_request_opened, pr_webhook, db)
        elif action == "closed":
            background_tasks.add_task(handle_pull_request_closed, pr_webhook, db)
        elif action == "synchronize":
            background_tasks.add_task(handle_pull_request_synchronize, pr_webhook, db)
        elif action == "reopened":
            background_tasks.add_task(handle_pull_request_reopened, pr_webhook, db)
        else:
            logger.info(f"Ignoring PR action: {action}")

        return {
            "status": "received",
            "event": x_github_event,
            "action": action,
            "pr": pr_webhook.number,
            "delivery_id": delivery_id
        }

    elif x_github_event == "ping":
        # GitHub sends this to test the webhook
        logger.info("Received ping event")
        return {"status": "pong"}

    else:
        logger.info(f"Ignoring event type: {x_github_event}")
        return {"status": "ignored", "event": x_github_event}
