"""
Which repositories a user can see previews for.

A preview belongs to a pull request, and the people who review pull
requests are the repository's collaborators. Ephemera does not keep users'
GitHub tokens, so access is established with the App's own credentials:
list the repositories the App is installed on, then ask GitHub whether the
user is a collaborator on each. Answers are cached for
REPO_ACCESS_CACHE_SECONDS because the environments list is polled.

Admins (ADMIN_GITHUB_LOGINS) see every installed repository.
"""

import logging
import threading
import time
from typing import Dict, List, Set, Tuple

from app.config import settings
from app.models import User
from app.services.github import GitHubUnavailable, InstalledRepository, github_service

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_installed: Tuple[float, List[InstalledRepository]] = (0.0, [])
_per_user: Dict[str, Tuple[float, List[InstalledRepository]]] = {}


def clear_cache() -> None:
    global _installed
    with _lock:
        _installed = (0.0, [])
        _per_user.clear()


def installed_repositories() -> List[InstalledRepository]:
    """All repositories the App is installed on. Raises GitHubUnavailable."""
    global _installed
    now = time.monotonic()
    with _lock:
        stamp, repos = _installed
        if repos and now - stamp < settings.repo_access_cache_seconds:
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
        if cached and now - cached[0] < settings.repo_access_cache_seconds:
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
