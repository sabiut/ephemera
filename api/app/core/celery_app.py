"""
Celery application configuration.

This module configures Celery for async task processing, including:
- Kubernetes operations (namespace creation/deletion)
- Environment provisioning
- Periodic cleanup jobs
"""

from celery import Celery
from app.config import settings

# Create Celery app
celery_app = Celery(
    "ephemera",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.environment",
        "app.tasks.cleanup"
    ]
)

# Configure Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=3600,  # 1 hour
    beat_schedule={
        "cleanup-stale-environments": {
            "task": "app.tasks.cleanup.cleanup_stale_environments",
            "schedule": 3600.0,  # Run every hour
        },
    },
)

# Task routes
celery_app.conf.task_routes = {
    "app.tasks.environment.*": {"queue": "environment"},
    "app.tasks.cleanup.*": {"queue": "cleanup"},
}
