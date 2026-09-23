"""
Which repositories a user can see previews for.

A preview belongs to a pull request, and the people who review pull
requests are the repository's collaborators. Ephemera does not keep users'
GitHub tokens, so access is established with the App's own credentials:
list the repositories the App is installed on, then ask GitHub whether the
user is a collaborator on each. Answers are cached for
REPO_ACCESS_CACHE_SECONDS because the environments list is polled.

Admins (ADMIN_GITHUB_LOGINS) see every installed repository.

Installing the App, or changing which repositories it covers, must show up
at once: a first-time user who installs it and reloads should not see an
empty list for five minutes. The API runs several replicas, each with its
own cache, so invalidation bumps a generation counter in Redis that every
replica compares before trusting its cache. An empty answer is also only
kept briefly, since it usually means "not installed yet".
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

import redis

from app.config import settings
from app.models import User
from app.services.github import GitHubUnavailable, InstalledRepository, github_service

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_installed: Tuple[float, List[InstalledRepository]] = (0.0, [])
_per_user: Dict[str, Tuple[float, List[InstalledRepository]]] = {}
_seen_generation: Optional[str] = None

GENERATION_KEY = "ephemera:repo-access-generation"


def clear_cache() -> None:
    global _installed
    with _lock:
        _installed = (0.0, [])
        _per_user.clear()


def _generation() -> Optional[str]:
    """The shared invalidation counter, or None if Redis cannot be read."""
    from app.core.locks import _redis  # one Redis client for the process
    try:
        value = _redis().get(GENERATION_KEY)
    except redis.RedisError as e:
        logger.debug(f"Repository access generation unavailable: {e}")
        return None
    return value.decode() if isinstance(value, bytes) else (str(value) if value is not None else "0")


def _sync_generation() -> None:
    """Drop this replica's cache if another replica invalidated since."""
    global _seen_generation
    current = _generation()
    if current is None:
        return  # Redis unreachable: fall back to the time-based expiry
    with _lock:
        stale = _seen_generation is not None and current != _seen_generation
        _seen_generation = current
    if stale:
        clear_cache()


def invalidate() -> None:
    """
    Forget repository access everywhere: this replica's cache now, and every
    other replica's on its next lookup. Called when the App's installations
    change and when a user asks to refresh.
    """
    from app.core.locks import _redis
    clear_cache()
    try:
        _redis().incr(GENERATION_KEY)
    except redis.RedisError as e:
        logger.warning(f"Could not signal other replicas to refresh repository access: {e}")


def _fresh(stamp: float, entries: list, now: float) -> bool:
    ttl = settings.repo_access_cache_seconds if entries else settings.repo_access_empty_cache_seconds
    return now - stamp < ttl


def installed_repositories() -> List[InstalledRepository]:
    """All repositories the App is installed on. Raises GitHubUnavailable."""
    global _installed
    _sync_generation()
    now = time.monotonic()
    with _lock:
        stamp, repos = _installed
        if stamp and _fresh(stamp, repos, now):
            return repos
    repos = sorted(github_service.list_installed_repositories(), key=lambda r: r.full_name.lower())
    with _lock:
        _installed = (now, repos)
    return repos


def accessible_repositories(user: User, admin: bool) -> List[InstalledRepository]:
    """
    Installed repositories this user may see: all of them for admins,
    otherwise those where GitHub reports the user as a collaborator.
    Raises GitHubUnavailable.
    """
    repos = installed_repositories()
    if admin:
        return repos
    key = user.github_login.lower()
    now = time.monotonic()
    with _lock:
        cached = _per_user.get(key)
        if cached and _fresh(cached[0], cached[1], now):
            return cached[1]
    visible = [
        r for r in repos
        if github_service.is_collaborator(r.installation_id, r.full_name, user.github_login) is True
    ]
    with _lock:
        _per_user[key] = (now, visible)
    return visible


def accessible_repo_names(user: User, admin: bool) -> Set[str]:
    """
    Names of repositories the user may see. Never raises: if GitHub cannot be
    asked, the user falls back to seeing only their own pull requests.
    """
    try:
        return {r.full_name for r in accessible_repositories(user, admin)}
    except GitHubUnavailable:
        return set()
    except Exception as e:
        logger.warning(f"Could not resolve repository access for {user.github_login}: {e}")
        return set()
