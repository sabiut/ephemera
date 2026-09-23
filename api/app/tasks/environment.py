"""
Celery tasks for environment management.

These tasks handle async operations for Kubernetes environments including:
- Namespace creation and provisioning
- Namespace deletion and cleanup
- Application (re)deployment
"""

import logging
from typing import Any, Dict, Optional

from celery import Task
from sqlalchemy.orm import Session

from app.config import settings
from app.core.celery_app import celery_app
from app.crud import deployment as deployment_crud
from app.crud import environment as environment_crud
from app.database import SessionLocal
from app.models.deployment import DeploymentStatus
from app.models.environment import EnvironmentStatus
from app.services import ai_deployment_service
from app.services.deployment import deployment_service
from app.services.github import github_service
from app.services.kubernetes import kubernetes_service
from app.services.deployment import choose_primary_url, probe_urls

logger = logging.getLogger(__name__)

STATUS_CONTEXT = "ephemera/environment"
FOOTER = "\n---\n*Powered by Ephemera*\n"


class DatabaseTask(Task):
    """Base task that provides a database session."""
    _db: Optional[Session] = None

    @property
    def db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def after_return(self, *args, **kwargs):
        """Clean up database session after task completes."""
        if self._db is not None:
            self._db.close()
            self._db = None


def _active_deployment_service():
    """AI service when enabled (it falls back internally), else deterministic."""
    if ai_deployment_service and ai_deployment_service.enabled:
        return ai_deployment_service
    return deployment_service


def _run_deployment(
    db: Session,
    environment_id: int,
    installation_id: int,
    repo_full_name: str,
    namespace: str,
    commit_sha: str,
) -> Dict[str, Any]:
    """Deploy the repo at ``commit_sha`` into ``namespace`` and record the result."""
    latest = deployment_crud.get_latest_deployment(db, environment_id)
    if latest:
        deployment_crud.update_deployment_status(db, latest, DeploymentStatus.IN_PROGRESS)

    result = _active_deployment_service().deploy_application(
        installation_id=installation_id,
        repo_full_name=repo_full_name,
        namespace=namespace,
        ref=commit_sha,
    )

    if result.get("ai_generated"):
        logger.info("Deployment used AI-generated manifests")
    elif result.get("ai_fallback_reason"):
        # The full reason (provider error, request id, billing text) stays
        # here in the logs; the PR comment only says that the fallback ran.
        logger.warning(f"AI manifest generation unavailable, used the compose converter: {result['ai_fallback_reason']}")

    # "Ready" has to mean a reviewer can open the preview. Applying manifests
    # is not that, so each of these turns an apparent success into a failure
    # with a reason a developer can act on.
    if result.get("success"):
        services = result.get("services") or []
        if not result.get("compose_found", True):
            result["success"] = False
            result["error"] = "No docker-compose.yml in the repository, so there is nothing to preview"
        elif not services:
            skipped = result.get("skipped_services") or []
            detail = (
                f"every service is build-only ({', '.join(skipped)}); Ephemera deploys images, "
                "so the repository's CI must publish an image and the compose file must reference it"
                if skipped else "the compose file defines no deployable services"
            )
            result["success"] = False
            result["error"] = f"Nothing was deployed: {detail}"
        else:
            def waiting_for_image(service: str, image: str) -> None:
                github_service.update_pr_status(
                    installation_id=installation_id,
                    repo_full_name=repo_full_name,
                    commit_sha=commit_sha,
                    state="pending",
                    description=f"Waiting for {service} image built from {commit_sha[:7]}",
                )

            ready, problems = kubernetes_service.wait_for_deployments_ready(
                namespace, services,
                timeout_seconds=settings.preview_ready_timeout_seconds,
                image_wait_seconds=settings.preview_image_wait_seconds,
                commit_markers=(commit_sha, commit_sha[:7]),
                on_waiting_for_image=waiting_for_image,
            )
            if problems:
                result["success"] = False
                result["error"] = "Services did not become ready: " + "; ".join(
                    f"{name} ({reason})" for name, reason in problems.items()
                )
            else:
                unreachable = probe_urls(
                    result.get("service_urls") or {},
                    timeout_seconds=settings.preview_ready_timeout_seconds,
                )
                if unreachable:
                    result["success"] = False
                    result["error"] = "Preview URLs did not answer: " + "; ".join(
                        f"{name} ({reason})" for name, reason in unreachable.items()
                    )

    result["commit_sha"] = commit_sha
    if not result.get("success") and not result.get("error"):
        result["error"] = "Application deployment failed without a reported reason"

    if result.get("success"):
        environment = environment_crud.get_environment(db, environment_id)
        urls = result.get("service_urls") or {}
        primary = choose_primary_url(result.get("services") or [], urls)
        result["primary_url"] = primary
        if environment:
            environment_crud.record_service_urls(db, environment, urls, primary)

    if latest:
        ok = bool(result.get("success"))
        deployment_crud.update_deployment_status(
            db=db,
            deployment=latest,
            status=DeploymentStatus.SUCCESS if ok else DeploymentStatus.FAILED,
            error_message=None if ok else result.get("error"),
            ai_generated=result.get("ai_generated", False),
            ai_plan=result.get("ai_plan"),
        )

    return result


def _deployment_summary(result: Dict[str, Any]) -> str:
    """Markdown block describing what was deployed, for PR comments."""
    lines = []
    services = result.get("services", [])
    urls = result.get("service_urls", {})
    if result.get("primary_url"):
        lines.append(f"\n**Open preview**: {result['primary_url']}")
    if result.get("commit_sha"):
        lines.append(f"**Commit**: `{result['commit_sha'][:7]}`")
    if services:
        lines.append("\n**Deployed Services**:")
        for service in services:
            lines.append(f"- **{service}**: {urls[service]}" if service in urls else f"- {service}")
        lines.append("")

    skipped = result.get("skipped_services", [])
    if skipped:
        lines.append(
            "\n> **Skipped** (build-only services need a pre-built image): "
            + ", ".join(f"`{s}`" for s in skipped) + "\n"
        )

    unpinned = result.get("unpinned_builds") or []
    if unpinned:
        names = ", ".join(f"`{n}`" for n in unpinned)
        lines.append(
            f"\n> **Not built from this commit**: {names} "
            f"{'has' if len(unpinned) == 1 else 'have'} a `build:` section, but the image is not tagged "
            "with `${EPHEMERA_SHA}`, so this preview may not contain the pull request's changes. "
            "Have CI push an image per commit and reference it as `image: <registry>/<name>:${EPHEMERA_SHA}`.\n"
        )
    unset = result.get("unset_variables") or []
    if unset:
        lines.append("\n> **Unset variables** (substituted as empty): " + ", ".join(f"`{v}`" for v in unset) + "\n")

    if result.get("ai_generated") and result.get("ai_plan"):
        lines.append(f"\n<details>\n<summary>AI Deployment Plan</summary>\n\n{result['ai_plan']}\n</details>\n")
    elif result.get("ai_fallback_reason") and result["ai_fallback_reason"] != "AI deployment disabled":
        # Deliberately no detail: the reason is a raw provider error and this
        # comment is public. It is logged by _run_deployment instead.
        lines.append("\n> **Note**: AI manifest generation unavailable, used the compose converter.\n")

    return "\n".join(lines)


def _notify(
    installation_id: Optional[int],
    repo_full_name: Optional[str],
    pr_number: Optional[int],
    commit_sha: Optional[str],
    state: str,
    description: str,
    comment: Optional[str],
    target_url: Optional[str] = None,
):
    """Best-effort GitHub status + PR comment."""
    if not (installation_id and repo_full_name):
        return
    if commit_sha:
        github_service.update_pr_status(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            commit_sha=commit_sha,
            state=state,
            description=description,
            context=STATUS_CONTEXT,
            target_url=target_url,
        )
    if pr_number and comment:
        github_service.post_comment_to_pr(installation_id, repo_full_name, pr_number, comment)


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.environment.provision_environment")
def provision_environment(
    self,
    environment_id: int,
    installation_id: Optional[int] = None,
    repo_full_name: Optional[str] = None,
    pr_number: Optional[int] = None,
    commit_sha: Optional[str] = None
):
    """
    Provision a new environment: create the namespace and quota, then deploy
    the repository's docker-compose services into it.
    """
    logger.info(f"Starting environment provisioning for environment {environment_id}")

    environment = environment_crud.get_environment(self.db, environment_id)
    if not environment:
        logger.error(f"Environment {environment_id} not found")
        return {"success": False, "error": "Environment not found"}

    installation_id = installation_id or environment.installation_id
    repo_full_name = repo_full_name or environment.repository_full_name
    pr_number = pr_number or environment.pr_number
    commit_sha = commit_sha or environment.commit_sha

    environment_crud.update_environment_status(self.db, environment, EnvironmentStatus.PROVISIONING)

    try:
        labels = {
            "app": "ephemera",
            "managed-by": "ephemera",
            "pr-number": str(pr_number),
            "repository": repo_full_name.split("/")[-1],
            "environment-id": str(environment.id),
        }
        if not kubernetes_service.create_namespace(environment.namespace, labels=labels):
            raise RuntimeError("Failed to create Kubernetes namespace")

        kubernetes_service.create_resource_quota(
            namespace=environment.namespace,
            cpu_limit=settings.preview_cpu_quota,
            memory_limit=settings.preview_memory_quota,
            pod_limit=settings.preview_pod_quota,
        )

        result = _run_deployment(
            self.db, environment_id, installation_id, repo_full_name, environment.namespace, commit_sha
        )

        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Application deployment failed")

        environment_crud.update_environment_status(self.db, environment, EnvironmentStatus.READY)
        logger.info(f"Environment {environment_id} provisioned successfully")

        env_url = result.get("primary_url") or environment.environment_url
        comment = f"""## Ephemera Environment Ready

Your preview environment has been created!

**Namespace**: `{environment.namespace}`
**Status**: Ready{_deployment_summary(result)}{FOOTER}"""
        _notify(installation_id, repo_full_name, pr_number, commit_sha,
                "success", "Preview environment ready", comment, target_url=env_url)

        return {
            "success": True,
            "environment_id": environment_id,
            "namespace": environment.namespace,
            "status": environment.status,
        }

    except Exception as e:
        logger.error(f"Failed to provision environment {environment_id}: {e}", exc_info=True)
        environment_crud.update_environment_status(
            self.db, environment, EnvironmentStatus.FAILED, error_message=str(e)
        )
        comment = f"""## Ephemera Environment Failed

Failed to create preview environment.

**Namespace**: `{environment.namespace}`
**Status**: Failed
**Error**: {e}

Fix the cause and push a new commit; Ephemera will try again from scratch.{FOOTER}"""
        _notify(installation_id, repo_full_name, pr_number, commit_sha,
                "failure", "Failed to create environment", comment)
        return {"success": False, "environment_id": environment_id, "error": str(e)}


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.environment.destroy_environment")
def destroy_environment(
    self,
    environment_id: int,
    installation_id: Optional[int] = None,
    repo_full_name: Optional[str] = None,
    pr_number: Optional[int] = None,
    pr_merged: bool = False
):
    """Destroy an environment by deleting its Kubernetes namespace."""
    logger.info(f"Starting environment destruction for environment {environment_id}")

    environment = environment_crud.get_environment(self.db, environment_id)
    if not environment:
        logger.error(f"Environment {environment_id} not found")
        return {"success": False, "error": "Environment not found"}

    environment_crud.update_environment_status(self.db, environment, EnvironmentStatus.DESTROYING)

    try:
        outcome = kubernetes_service.delete_namespace(environment.namespace)
        if outcome == kubernetes_service.DELETE_REFUSED:
            raise RuntimeError(f"Refused to delete {environment.namespace}: it is not an Ephemera namespace")
        if outcome == kubernetes_service.DELETE_ERROR:
            # Stay DESTROYING: the hourly cleanup retries the deletion. Saying
            # "Destroyed" here is how a failed API call used to leave
            # previews running with no record of them.
            environment.error_message = "Namespace deletion failed; will retry"
            self.db.commit()
            logger.error(f"Deleting {environment.namespace} failed; leaving environment {environment_id} DESTROYING")
            return {"success": False, "environment_id": environment_id, "error": "namespace deletion failed"}
        if outcome == kubernetes_service.DELETE_STARTED and not kubernetes_service.wait_for_namespace_gone(
            environment.namespace, timeout_seconds=settings.preview_destroy_confirm_seconds
        ):
            logger.warning(f"{environment.namespace} still terminating; leaving environment {environment_id} DESTROYING")
            return {"success": False, "environment_id": environment_id, "error": "namespace still terminating"}

        environment_crud.update_environment_status(self.db, environment, EnvironmentStatus.DESTROYED)
        logger.info(f"Environment {environment_id} destroyed; namespace {environment.namespace} is gone")

        if installation_id and repo_full_name and pr_number:
            action = "merged" if pr_merged else "closed"
            comment = f"""## Environment Cleanup Complete

PR was {action}. Preview environment has been destroyed.

**Namespace**: `{environment.namespace}`
**Status**: Destroyed{FOOTER}"""
            github_service.post_comment_to_pr(installation_id, repo_full_name, pr_number, comment)

        return {
            "success": True,
            "environment_id": environment_id,
            "namespace": environment.namespace,
            "status": environment.status,
        }

    except Exception as e:
        logger.error(f"Failed to destroy environment {environment_id}: {e}", exc_info=True)
        environment_crud.update_environment_status(
            self.db, environment, EnvironmentStatus.FAILED, error_message=str(e)
        )
        return {"success": False, "environment_id": environment_id, "error": str(e)}


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.environment.update_environment")
def update_environment(
    self,
    environment_id: int,
    commit_sha: str,
    installation_id: Optional[int] = None,
    repo_full_name: Optional[str] = None,
    pr_number: Optional[int] = None
):
    """Redeploy an existing environment at a new commit."""
    logger.info(f"Updating environment {environment_id} for commit {commit_sha}")

    environment = environment_crud.get_environment(self.db, environment_id)
    if not environment:
        logger.error(f"Environment {environment_id} not found")
        return {"success": False, "error": "Environment not found"}

    installation_id = installation_id or environment.installation_id
    repo_full_name = repo_full_name or environment.repository_full_name
    pr_number = pr_number or environment.pr_number

    try:
        exists = kubernetes_service.namespace_exists(environment.namespace)
        if exists is False:
            raise RuntimeError(f"Namespace {environment.namespace} no longer exists")

        result = _run_deployment(
            self.db, environment_id, installation_id, repo_full_name, environment.namespace, commit_sha
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Application deployment failed")

        environment_crud.update_environment_status(self.db, environment, EnvironmentStatus.READY)
        logger.info(f"Environment {environment_id} redeployed at {commit_sha[:8]}")

        env_url = result.get("primary_url") or environment.environment_url
        comment = f"""## Ephemera Environment Updated

Redeployed at `{commit_sha[:8]}`.

**Namespace**: `{environment.namespace}`
**Status**: Ready{_deployment_summary(result)}{FOOTER}"""
        _notify(installation_id, repo_full_name, pr_number, commit_sha,
                "success", "Preview environment updated", comment, target_url=env_url)

        return {
            "success": True,
            "environment_id": environment_id,
            "namespace": environment.namespace,
            "status": environment.status,
        }

    except Exception as e:
        logger.error(f"Failed to update environment {environment_id}: {e}", exc_info=True)
        environment_crud.update_environment_status(
            self.db, environment, EnvironmentStatus.FAILED, error_message=str(e)
        )
        comment = f"""## Ephemera Environment Update Failed

Could not redeploy at `{commit_sha[:8]}`.

**Namespace**: `{environment.namespace}`
**Error**: {e}

Fix the cause and push a new commit; Ephemera will try again from scratch.{FOOTER}"""
        _notify(installation_id, repo_full_name, pr_number, commit_sha,
                "failure", "Failed to update environment", comment)
        return {"success": False, "environment_id": environment_id, "error": str(e)}
