"""
Per-environment lock, so only one task changes a preview at a time.

Pushes arriving close together queue one task each. Without a lock two
tasks could apply manifests to the same namespace at once, and an older
commit's task could finish last and overwrite the newer result. Redis is
already the Celery broker, so the lock lives there.

The lock never fails open. A worker can already hold a task when Redis
drops, so "no broker, no tasks" does not hold; a task that cannot establish
the lock reports it and is retried later without touching the cluster.
"""

import logging
import ssl
from contextlib import contextmanager
from typing import Iterator, Optional

import redis

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional["redis.Redis"] = None

HELD = "held"                # this task holds the lock
BUSY = "busy"                # another task held it for the whole wait
UNAVAILABLE = "unavailable"  # Redis could not be reached


def _redis() -> "redis.Redis":
    global _client
    if _client is None:
        kwargs = {}
        if settings.redis_url.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED if settings.redis_ssl_verify else ssl.CERT_NONE
        _client = redis.Redis.from_url(settings.redis_url, socket_timeout=10, **kwargs)
    return _client


@contextmanager
def environment_lock(environment_id: int) -> Iterator[str]:
    """
    Hold the environment's lock for the duration of the block. Yields HELD,
    BUSY (not acquired within ENVIRONMENT_LOCK_WAIT_SECONDS) or UNAVAILABLE
    (Redis error). Only HELD permits changing the preview.
    """
    name = f"ephemera:environment-lock:{environment_id}"
    try:
        lock = _redis().lock(
            name,
            timeout=settings.environment_lock_seconds,
            blocking_timeout=settings.environment_lock_wait_seconds,
        )
        acquired = lock.acquire()
    except redis.RedisError as e:
        logger.warning(f"Environment lock unavailable ({e})")
        yield UNAVAILABLE
        return
    if not acquired:
        yield BUSY
        return
    try:
        yield HELD
    finally:
        try:
            lock.release()
        except redis.RedisError as e:  # expired or connection lost; it times out on its own
            logger.warning(f"Could not release {name}: {e}")
