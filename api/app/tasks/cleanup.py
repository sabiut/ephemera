"""
Celery tasks for periodic cleanup operations.

These tasks handle:
- Stale environment cleanup
- Failed environment retry
- Resource quota monitoring
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from typing import List
from celery import Task
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.celery_app import celery_app
from app.database import SessionLocal
from app.services.kubernetes import kubernetes_service
from app.crud import environment as environment_crud
from app.models.environment import Environment, EnvironmentStatus

logger = logging.getLogger(__name__)


class DatabaseTask(Task):
    """Base task that provides a database session."""
    _db: Session = None

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


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.cleanup.cleanup_stale_environments")
def cleanup_stale_environments(self):
    """
    Clean up environments that have been in provisioning/destroying state for too long.

    This task runs periodically to find and clean up:
    - Environments stuck in PROVISIONING state for > 30 minutes
    - Environments stuck in DESTROYING state for > 30 minutes
    - Environments in READY state but namespace doesn't exist
    """
    logger.info("Starting stale environment cleanup")

    stale_threshold = datetime.now(timezone.utc) - timedelta(minutes=30)
    cleaned_count = 0

    try:
        # Find environments stuck in PROVISIONING
        provisioning_envs = self.db.query(Environment).filter(
            and_(
                Environment.status == EnvironmentStatus.PROVISIONING,
                Environment.updated_at < stale_threshold
            )
        ).all()

        logger.info(f"Found {len(provisioning_envs)} environments stuck in PROVISIONING")

        for env in provisioning_envs:
            logger.warning(f"Cleaning up stale environment {env.id} stuck in PROVISIONING")

            # Remove whatever the stuck attempt created; a missing namespace is fine
            kubernetes_service.delete_namespace(env.namespace)

            # Mark as failed
            environment_crud.update_environment_status(
                db=self.db,
                environment=env,
                status=EnvironmentStatus.FAILED,
                error_message="Environment stuck in provisioning state"
            )
            cleaned_count += 1

        # Find environments stuck in DESTROYING
        destroying_envs = self.db.query(Environment).filter(
            and_(
                Environment.status == EnvironmentStatus.DESTROYING,
                Environment.updated_at < stale_threshold
            )
        ).all()

        logger.info(f"Found {len(destroying_envs)} environments stuck in DESTROYING")

        for env in destroying_envs:
            logger.warning(f"Cleaning up stale environment {env.id} stuck in DESTROYING")

            # Only a namespace confirmed absent makes the environment
            # DESTROYED. Otherwise request deletion again and check next hour.
            outcome = kubernetes_service.delete_namespace(env.namespace)
            if outcome == kubernetes_service.DELETE_ABSENT:
                environment_crud.update_environment_status(
                    db=self.db,
                    environment=env,
                    status=EnvironmentStatus.DESTROYED
                )
                cleaned_count += 1
            else:
                logger.warning(f"{env.namespace} not gone yet ({outcome}); still DESTROYING")

        # Find environments in READY state but namespace doesn't exist
        ready_envs = self.db.query(Environment).filter(
            Environment.status == EnvironmentStatus.READY
        ).all()

        logger.info(f"Checking {len(ready_envs)} READY environments for namespace existence")

        for env in ready_envs:
            # None means "unknown" (API error); only act on a definite miss
            if kubernetes_service.namespace_exists(env.namespace) is False:
                logger.warning(f"Environment {env.id} in READY state but namespace {env.namespace} doesn't exist")

                # Mark as failed
                environment_crud.update_environment_status(
                    db=self.db,
                    environment=env,
                    status=EnvironmentStatus.FAILED,
                    error_message="Namespace no longer exists"
                )
                cleaned_count += 1

        logger.info(f"Stale environment cleanup completed. Cleaned {cleaned_count} environments")

        return {
            "success": True,
            "cleaned_count": cleaned_count,
            "provisioning_stuck": len(provisioning_envs),
            "destroying_stuck": len(destroying_envs)
        }

    except Exception as e:
        logger.error(f"Error during stale environment cleanup: {e}")
        return {
            "success": False,
            "error": str(e),
            "cleaned_count": cleaned_count
        }


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.cleanup.cleanup_old_environments")
def cleanup_old_environments(self, days: int = 7):
    """
    Clean up environments that have been destroyed for more than X days.

    Args:
        days: Number of days to keep destroyed environments (default: 7)
    """
    logger.info(f"Starting cleanup of environments destroyed more than {days} days ago")

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    deleted_count = 0

    try:
        # Find old destroyed environments
        old_envs = self.db.query(Environment).filter(
            and_(
                Environment.status == EnvironmentStatus.DESTROYED,
                Environment.updated_at < cutoff_date
            )
        ).all()

        logger.info(f"Found {len(old_envs)} old destroyed environments to clean up")

        for env in old_envs:
            logger.info(f"Deleting old environment record {env.id}")
            self.db.delete(env)
            deleted_count += 1

        self.db.commit()

        logger.info(f"Cleanup completed. Deleted {deleted_count} environment records")

        return {
            "success": True,
            "deleted_count": deleted_count
        }

    except Exception as e:
        logger.error(f"Error during old environment cleanup: {e}")
        self.db.rollback()
        return {
            "success": False,
            "error": str(e),
            "deleted_count": deleted_count
        }


# Failure reasons worth one automatic retry: they can clear up on their own
# (a slow node, a certificate still being issued, a brief API outage).
# Everything else, such as a missing compose file, a build-only service,
# a crash loop or an image that was never published, fails the same way
# every time and needs a push from the developer instead.
TRANSIENT_FAILURE_MARKERS = (
    "Preview URLs did not answer",
    "pod is still Pending",
    "readiness probe has not passed",
    "pods did not become ready in time",
    "Failed to create Kubernetes namespace",
    "timed out",
    "Timeout",
    "ConnectError",
    "Connection",
)


def is_transient_failure(error_message: Optional[str]) -> bool:
    return bool(error_message) and any(m in error_message for m in TRANSIENT_FAILURE_MARKERS)


def _pull_request_state(env) -> Optional[str]:
    """"open" or "closed" from GitHub, or None if GitHub could not be asked."""
    from app.services.github import github_service
    try:
        pr = github_service.get_pull_request(env.installation_id, env.repository_full_name, env.pr_number)
    except Exception as e:
        logger.warning(f"Could not read PR state for environment {env.id}: {e}")
        return None
    if pr is None:
        return "closed"  # deleted or no longer visible: treat as gone
    return "open" if pr.state == "open" else "closed"


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.cleanup.retry_failed_environments")
def retry_failed_environments(self, max_age_hours: int = 1):
    """
    Retry recently failed environments once, when the failure looks transient.

    An environment is retried only if its error matches a transient marker
    and its current commit has exactly one deployment attempt. The retry
    records a second attempt, so no environment is retried twice for the
    same commit and a deterministic failure never loops.
    """
    from app.crud import deployment as deployment_crud
    from app.models import Deployment
    from app.tasks.environment import provision_environment  # avoid a circular import

    cutoff_date = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    retried: list = []
    skipped = 0
    try:
        failed_envs = self.db.query(Environment).filter(
            and_(Environment.status == EnvironmentStatus.FAILED, Environment.updated_at > cutoff_date)
        ).all()
        for env in failed_envs:
            attempts = self.db.query(Deployment).filter(
                Deployment.environment_id == env.id, Deployment.commit_sha == env.commit_sha
            ).count()
            if attempts != 1 or not is_transient_failure(env.error_message):
                skipped += 1
                continue
            state = _pull_request_state(env)
            if state == "closed":
                # The close webhook may have been missed or the environment
                # failed before it arrived: clean up instead of retrying.
                from app.tasks.environment import destroy_environment
                environment_crud.mark_closed(self.db, env)
                destroy_environment.delay(environment_id=env.id)
                logger.info(f"PR for environment {env.id} is closed; destroying instead of retrying")
                skipped += 1
                continue
            if state != "open":
                skipped += 1  # could not ask GitHub; never retry blind
                continue
            record = deployment_crud.create_deployment(self.db, env, env.commit_sha)
            provision_environment.delay(environment_id=env.id, commit_sha=env.commit_sha, deployment_id=record.id)
            retried.append(env.id)
            logger.info(f"Retrying environment {env.id} once after transient failure: {env.error_message}")
        return {"success": True, "retry_count": len(retried), "retried": retried, "skipped": skipped}
    except Exception as e:
        logger.error(f"Error during failed environment retry: {e}")
        return {"success": False, "error": str(e), "retry_count": len(retried)}
