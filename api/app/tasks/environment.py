"""
Celery tasks for environment management.

These tasks handle async operations for Kubernetes environments including:
- Namespace creation and provisioning
- Namespace deletion and cleanup
- Application deployment
"""

import logging
from typing import Optional
from celery import Task
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.database import SessionLocal
from app.services.kubernetes import kubernetes_service
from app.services.github import github_service
from app.services.deployment import deployment_service
from app.services import ai_deployment_service
from app.crud import environment as environment_crud
from app.crud import deployment as deployment_crud
from app.models.environment import EnvironmentStatus

logger = logging.getLogger(__name__)


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
    Provision a new environment by creating Kubernetes namespace and resources.

    Args:
        environment_id: Database ID of environment to provision
        installation_id: GitHub installation ID for API calls
        repo_full_name: Full repository name (owner/repo)
        pr_number: Pull request number
        commit_sha: Commit SHA for status updates
    """
    logger.info(f"Starting environment provisioning for environment {environment_id}")

    # Get environment from database
    environment = environment_crud.get_environment(self.db, environment_id)
    if not environment:
        logger.error(f"Environment {environment_id} not found")
        return {"success": False, "error": "Environment not found"}

    # Update status to provisioning
    environment_crud.update_environment_status(
        db=self.db,
        environment=environment,
        status=EnvironmentStatus.PROVISIONING
    )

    try:
        # Create Kubernetes namespace
        k8s_labels = {
            "app": "ephemera",
            "pr-number": str(pr_number) if pr_number else "",
            "repository": repo_full_name.split("/")[-1] if repo_full_name else "",
            "environment-id": str(environment.id)
        }

        k8s_success = kubernetes_service.create_namespace(
            namespace=environment.namespace,
            labels=k8s_labels
        )

        if not k8s_success:
            raise Exception("Failed to create Kubernetes namespace")

        # Create resource quota
        kubernetes_service.create_resource_quota(
            namespace=environment.namespace,
            cpu_limit="1",
            memory_limit="2Gi",
            pod_limit="10"
        )

        # Deploy application from docker-compose.yml if GitHub credentials provided
        deployed_services = []
        service_urls = {}
        deployment_result = {}
        if installation_id and repo_full_name and commit_sha:
            logger.info(f"Deploying application to namespace {environment.namespace}")

            # Use AI deployment service if available, falls back to deterministic internally
            active_service = (
                ai_deployment_service
                if ai_deployment_service and ai_deployment_service.enabled
                else deployment_service
            )

            deployment_result = active_service.deploy_application(
                installation_id=installation_id,
                repo_full_name=repo_full_name,
                namespace=environment.namespace,
                ref=commit_sha
            )

            if deployment_result.get("ai_generated"):
                logger.info("Deployment used AI-generated manifests")
            elif deployment_result.get("ai_fallback_reason"):
                logger.info(f"AI fallback: {deployment_result['ai_fallback_reason']}")

            if deployment_result.get("success"):
                deployed_services = deployment_result.get("services", [])
                service_urls = deployment_result.get("service_urls", {})
                logger.info(f"Deployed {len(deployed_services)} services: {', '.join(deployed_services)}")
                if service_urls:
                    logger.info(f"Service URLs: {service_urls}")
            else:
                # Log warning but don't fail - namespace still created
                logger.warning(f"Failed to deploy application: {deployment_result.get('error')}")

        # Update deployment record with AI metadata
        if deployment_result:
            latest_deployment = deployment_crud.get_latest_deployment(self.db, environment_id)
            if latest_deployment:
                from app.models.deployment import DeploymentStatus
                deployment_crud.update_deployment_status(
                    db=self.db,
                    deployment=latest_deployment,
                    status=DeploymentStatus.SUCCESS if deployment_result.get("success") else DeploymentStatus.FAILED,
                    error_message=deployment_result.get("error"),
                    ai_generated=deployment_result.get("ai_generated", False),
                    ai_plan=deployment_result.get("ai_plan"),
                )

        # Update environment status to ready
        environment_crud.update_environment_status(
            db=self.db,
            environment=environment,
            status=EnvironmentStatus.READY
        )

        logger.info(f"Environment {environment_id} provisioned successfully")

        # Update GitHub status if credentials provided
        if installation_id and repo_full_name and commit_sha:
            env_url = github_service.build_environment_url(pr_number, repo_full_name.split("/")[-1])

            github_service.update_pr_status(
                installation_id=installation_id,
                repo_full_name=repo_full_name,
                commit_sha=commit_sha,
                state="success",
                description="Preview environment ready",
                context="ephemera/environment",
                target_url=env_url
            )

            if pr_number:
                # Build deployment info section
                deployment_info = ""
                if deployed_services:
                    # Build service URLs section
                    if service_urls:
                        services_with_urls = []
                        for service in deployed_services:
                            if service in service_urls:
                                services_with_urls.append(f"- **{service}**: {service_urls[service]}")
                            else:
                                services_with_urls.append(f"- {service}")
                        services_list = "\n".join(services_with_urls)
                        deployment_info = f"\n**Deployed Services**:\n{services_list}\n"
                    else:
                        services_list = "\n".join([f"- {service}" for service in deployed_services])
                        deployment_info = f"\n**Deployed Services**:\n{services_list}\n"
                elif installation_id and repo_full_name:
                    deployment_info = "\n⚠️ **Note**: No docker-compose.yml found. Namespace created but no application deployed.\n"

                # Build AI deployment plan section
                ai_section = ""
                if deployment_result.get("ai_generated"):
                    ai_plan = deployment_result.get("ai_plan", "")
                    if ai_plan:
                        ai_section = f"\n<details>\n<summary>AI Deployment Plan</summary>\n\n{ai_plan}\n</details>\n"
                elif deployment_result.get("ai_fallback_reason"):
                    ai_section = f"\n> **Note**: AI deployment unavailable ({deployment_result['ai_fallback_reason']}). Used deterministic converter.\n"

                comment = f"""## Ephemera Environment Ready

Your preview environment has been created!

**Namespace**: `{environment.namespace}`
**Status**: Ready{deployment_info}{ai_section}
---
*Powered by Ephemera*
"""
                github_service.post_comment_to_pr(
                    installation_id=installation_id,
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    comment=comment
                )

        return {
            "success": True,
            "environment_id": environment_id,
            "namespace": environment.namespace,
            "status": environment.status
        }

    except Exception as e:
        logger.error(f"Failed to provision environment {environment_id}: {e}")

        # Update environment status to failed
        environment_crud.update_environment_status(
            db=self.db,
            environment=environment,
            status=EnvironmentStatus.FAILED,
            error_message=str(e)
        )

        # Update GitHub status if credentials provided
        if installation_id and repo_full_name and commit_sha:
            github_service.update_pr_status(
                installation_id=installation_id,
                repo_full_name=repo_full_name,
                commit_sha=commit_sha,
                state="failure",
                description="Failed to create environment",
                context="ephemera/environment"
            )

            if pr_number:
                comment = f"""## Ephemera Environment Failed

Failed to create preview environment.

**Namespace**: `{environment.namespace}`
**Status**: Failed
**Error**: {str(e)}

Please check logs or contact support.

---
*Powered by Ephemera*
"""
                github_service.post_comment_to_pr(
                    installation_id=installation_id,
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    comment=comment
                )

        return {
            "success": False,
            "environment_id": environment_id,
            "error": str(e)
        }


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.environment.destroy_environment")
def destroy_environment(
    self,
    environment_id: int,
    installation_id: Optional[int] = None,
    repo_full_name: Optional[str] = None,
    pr_number: Optional[int] = None,
    pr_merged: bool = False
):
    """
    Destroy an environment by deleting Kubernetes namespace and resources.

    Args:
        environment_id: Database ID of environment to destroy
        installation_id: GitHub installation ID for API calls
        repo_full_name: Full repository name (owner/repo)
        pr_number: Pull request number
        pr_merged: Whether the PR was merged (vs closed)
    """
    logger.info(f"Starting environment destruction for environment {environment_id}")

    # Get environment from database
    environment = environment_crud.get_environment(self.db, environment_id)
    if not environment:
        logger.error(f"Environment {environment_id} not found")
        return {"success": False, "error": "Environment not found"}

    # Update status to destroying
    environment_crud.update_environment_status(
        db=self.db,
        environment=environment,
        status=EnvironmentStatus.DESTROYING
    )

    try:
        # Delete Kubernetes namespace
        k8s_success = kubernetes_service.delete_namespace(environment.namespace)

        if not k8s_success:
            raise Exception("Failed to delete Kubernetes namespace")

        # Update environment status to destroyed
        environment_crud.update_environment_status(
            db=self.db,
            environment=environment,
            status=EnvironmentStatus.DESTROYED
        )

        logger.info(f"Environment {environment_id} destroyed successfully")

        # Update GitHub with cleanup success
        if installation_id and repo_full_name and pr_number:
            action = "merged" if pr_merged else "closed"
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
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                comment=comment
            )

        return {
            "success": True,
            "environment_id": environment_id,
            "namespace": environment.namespace,
            "status": environment.status
        }

    except Exception as e:
        logger.error(f"Failed to destroy environment {environment_id}: {e}")

        # Update environment status to failed
        environment_crud.update_environment_status(
            db=self.db,
            environment=environment,
            status=EnvironmentStatus.FAILED,
            error_message=str(e)
        )

        return {
            "success": False,
            "environment_id": environment_id,
            "error": str(e)
        }


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.environment.update_environment")
def update_environment(
    self,
    environment_id: int,
    commit_sha: str,
    installation_id: Optional[int] = None,
    repo_full_name: Optional[str] = None,
    pr_number: Optional[int] = None
):
    """
    Update an environment with new commit (verify namespace exists).

    Args:
        environment_id: Database ID of environment to update
        commit_sha: New commit SHA
        installation_id: GitHub installation ID for API calls
        repo_full_name: Full repository name (owner/repo)
        pr_number: Pull request number
    """
    logger.info(f"Updating environment {environment_id} for commit {commit_sha}")

    # Get environment from database
    environment = environment_crud.get_environment(self.db, environment_id)
    if not environment:
        logger.error(f"Environment {environment_id} not found")
        return {"success": False, "error": "Environment not found"}

    try:
        # Verify namespace still exists
        namespace_exists = kubernetes_service.namespace_exists(environment.namespace)

        if namespace_exists:
            # Update environment status to ready
            environment_crud.update_environment_status(
                db=self.db,
                environment=environment,
                status=EnvironmentStatus.READY
            )

            logger.info(f"Environment {environment_id} ready for new deployment")

            # Update GitHub status
            if installation_id and repo_full_name:
                env_url = github_service.build_environment_url(
                    pr_number,
                    repo_full_name.split("/")[-1]
                )

                github_service.update_pr_status(
                    installation_id=installation_id,
                    repo_full_name=repo_full_name,
                    commit_sha=commit_sha,
                    state="success",
                    description="Environment ready for new commits",
                    context="ephemera/environment",
                    target_url=env_url
                )

            return {
                "success": True,
                "environment_id": environment_id,
                "namespace": environment.namespace,
                "status": environment.status
            }
        else:
            # Namespace was deleted
            environment_crud.update_environment_status(
                db=self.db,
                environment=environment,
                status=EnvironmentStatus.FAILED,
                error_message="Namespace no longer exists"
            )

            logger.error(f"Namespace {environment.namespace} not found")

            # Update GitHub status
            if installation_id and repo_full_name:
                github_service.update_pr_status(
                    installation_id=installation_id,
                    repo_full_name=repo_full_name,
                    commit_sha=commit_sha,
                    state="failure",
                    description="Environment namespace not found",
                    context="ephemera/environment"
                )

            return {
                "success": False,
                "environment_id": environment_id,
                "error": "Namespace not found"
            }

    except Exception as e:
        logger.error(f"Failed to update environment {environment_id}: {e}")

        environment_crud.update_environment_status(
            db=self.db,
            environment=environment,
            status=EnvironmentStatus.FAILED,
            error_message=str(e)
        )

        return {
            "success": False,
            "environment_id": environment_id,
            "error": str(e)
        }
