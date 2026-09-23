"""
Per-environment lock, so only one task changes a preview at a time.

Pushes arriving close together queue one task each. Without a lock two
tasks could apply manifests to the same namespace at once, and an older
commit's task could finish last and overwrite the newer result. Redis is
already the Celery broker, so the lock lives there.

If Redis cannot be reached the lock fails open with a warning: the broker
being down means no tasks run anyway, and a lock error must not turn a
routine deploy into a failure.
"""

import logging
import ssl
from contextlib import contextmanager
from typing import Iterator, Optional

import redis

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional["redis.Redis"] = None


def _redis() -> "redis.Redis":
    global _client
    if _client is None:
        kwargs = {}
        if settings.redis_url.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED if settings.redis_ssl_verify else ssl.CERT_NONE
        _client = redis.Redis.from_url(settings.redis_url, socket_timeout=10, **kwargs)
    return _client


@contextmanager
def environment_lock(environment_id: int) -> Iterator[bool]:
    """
    Hold the environment's lock for the duration of the block. Yields True
    when held (or when Redis is unreachable, failing open) and False if it
    could not be acquired within ENVIRONMENT_LOCK_WAIT_SECONDS.
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
        logger.warning(f"Environment lock unavailable ({e}); continuing without it")
        yield True
        return
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        try:
            lock.release()
        except redis.RedisError as e:  # expired or connection lost; it times out on its own
            logger.warning(f"Could not release {name}: {e}")
