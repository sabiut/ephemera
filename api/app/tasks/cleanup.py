"""
Celery tasks for periodic cleanup operations.

These tasks handle:
- Stale environment cleanup
- Failed environment retry
- Resource quota monitoring
"""

import logging
from datetime import datetime, timedelta
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

    stale_threshold = datetime.utcnow() - timedelta(minutes=30)
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

            # Try to delete namespace if it exists
            if kubernetes_service.namespace_exists(env.namespace):
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

            # Force delete namespace
            if kubernetes_service.namespace_exists(env.namespace):
                kubernetes_service.delete_namespace(env.namespace)

            # Mark as destroyed
            environment_crud.update_environment_status(
                db=self.db,
                environment=env,
                status=EnvironmentStatus.DESTROYED
            )
            cleaned_count += 1

        # Find environments in READY state but namespace doesn't exist
        ready_envs = self.db.query(Environment).filter(
            Environment.status == EnvironmentStatus.READY
        ).all()

        logger.info(f"Checking {len(ready_envs)} READY environments for namespace existence")

        for env in ready_envs:
            if not kubernetes_service.namespace_exists(env.namespace):
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

    cutoff_date = datetime.utcnow() - timedelta(days=days)
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


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.cleanup.retry_failed_environments")
def retry_failed_environments(self, max_age_hours: int = 1):
    """
    Retry provisioning for recently failed environments.

    Args:
        max_age_hours: Only retry environments that failed within this many hours (default: 1)
    """
    logger.info(f"Looking for failed environments to retry (within last {max_age_hours} hours)")

    cutoff_date = datetime.utcnow() - timedelta(hours=max_age_hours)
    retry_count = 0

    try:
        # Find recently failed environments
        failed_envs = self.db.query(Environment).filter(
            and_(
                Environment.status == EnvironmentStatus.FAILED,
                Environment.updated_at > cutoff_date
            )
        ).all()

        logger.info(f"Found {len(failed_envs)} recently failed environments")

        for env in failed_envs:
            logger.info(f"Retrying provisioning for environment {env.id}")

            # Import here to avoid circular dependency
            from app.tasks.environment import provision_environment

            # Trigger provisioning task
            provision_environment.delay(environment_id=env.id)
            retry_count += 1

        logger.info(f"Queued {retry_count} environments for retry")

        return {
            "success": True,
            "retry_count": retry_count
        }

    except Exception as e:
        logger.error(f"Error during failed environment retry: {e}")
        return {
            "success": False,
            "error": str(e),
            "retry_count": retry_count
        }
