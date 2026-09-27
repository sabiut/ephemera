"""
When a preview was last used and when it will expire.

Shared by the hourly expiry job and the API, so the date the dashboard shows
is the one the job acts on. Activity is the newest of: queued (a push or a
request), deployed, created, and "Keep available".
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import settings


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def last_activity(environment) -> Optional[datetime]:
    """When the preview was last requested, deployed or kept (UTC)."""
    stamps = [t for t in (environment.deploy_started_at, environment.last_deployed_at,
                          environment.created_at, getattr(environment, "kept_at", None)) if t]
    return max(_aware(t) for t in stamps) if stamps else None


def expires_at(environment) -> Optional[datetime]:
    """When an idle preview will be removed, or None if expiry is off."""
    days = settings.preview_idle_days
    last = last_activity(environment)
    if days <= 0 or last is None:
        return None
    return last + timedelta(days=days)


def is_idle(environment, now: Optional[datetime] = None) -> bool:
    """No push, deploy or Keep available for PREVIEW_IDLE_DAYS (never, when that is 0)."""
    expiry = expires_at(environment)
    return expiry is not None and (now or datetime.now(timezone.utc)) > expiry
