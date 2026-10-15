"""
Pipelines without stored secrets (docs/ci-pipeline.md, step 6): a GitHub
Actions job proves which repository it runs in with its OIDC identity token,
and gets a short-lived pipeline token for that repository's previews.

The identity token is signed by GitHub (verified against its published keys),
issued for Ephemera's audience, unexpired, and names the repository in its
`repository` claim. The repository must have the Ephemera App installed and
must have allowed it (repository_settings.github_actions_oidc): anyone who can
run a workflow in it can then deploy its previews, which is the trust a
stored pipeline token in its secrets would give too.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

import jwt
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.crud import user as user_crud
from app.models import APIToken, RepositorySettings

logger = logging.getLogger(__name__)

TOKEN_TYPE = "oidc"


class OIDCError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass
class Exchanged:
    token: str
    repository: str
    expires_at: datetime
    actor: str


_jwks: Dict[str, jwt.PyJWKClient] = {}


def _signing_key(token: str):
    """GitHub's key for this token, from its published JWKS (cached by PyJWKClient)."""
    issuer = settings.github_actions_oidc_issuer.rstrip("/")
    client = _jwks.get(issuer)
    if client is None:
        client = _jwks[issuer] = jwt.PyJWKClient(f"{issuer}/.well-known/jwks", cache_keys=True, lifespan=3600)
    try:
        return client.get_signing_key_from_jwt(token).key
    except jwt.PyJWKClientError as e:
        raise OIDCError(f"the identity token's signing key isn't GitHub's: {e}")


def verify(token: str, key_for: Callable[[str], Any] = None) -> Dict[str, Any]:
    """The claims of a genuine, current GitHub Actions identity token for Ephemera; else OIDCError."""
    if not token or token.count(".") != 2:
        raise OIDCError("that isn't an identity token")
    key = (key_for or _signing_key)(token)
    try:
        return jwt.decode(token, key, algorithms=["RS256"], audience=settings.oidc_audience,
                          issuer=settings.github_actions_oidc_issuer.rstrip("/"),
                          options={"require": ["exp", "iat", "iss", "aud", "repository", "actor", "actor_id"]},
                          leeway=30)
    except jwt.ExpiredSignatureError:
        raise OIDCError("the identity token has expired")
    except jwt.InvalidAudienceError:
        raise OIDCError(f"the identity token is not for Ephemera: request it with audience "
                        f"'{settings.oidc_audience}'")
    except jwt.InvalidIssuerError:
        raise OIDCError("the identity token was not issued by GitHub Actions")
    except jwt.PyJWTError as e:
        raise OIDCError(f"the identity token is not valid: {e}")


def allowed(db: Session, repository_full_name: str) -> bool:
    row = db.query(RepositorySettings).filter(
        func.lower(RepositorySettings.repository_full_name) == repository_full_name.lower()).first()
    return bool(row and row.github_actions_oidc)


def exchange(db: Session, identity_token: str, installed_repository: Optional[Callable[[str], Any]] = None,
             key_for: Callable[[str], Any] = None) -> Exchanged:
    """
    Verify the identity token and issue a pipeline token for its repository.
    installed_repository(full_name) returns the installed repository (with
    GitHub's spelling of the name) or None.
    """
    claims = verify(identity_token, key_for)
    claimed = str(claims.get("repository") or "")
    installed = (installed_repository or _installed)(claimed)
    if installed is None:
        raise OIDCError(f"the Ephemera App isn't installed on {claimed}", 404)
    if not allowed(db, installed.full_name):
        raise OIDCError(f"{installed.full_name} doesn't let GitHub Actions deploy previews with OIDC; a "
                        "collaborator can allow it on its Repositories page (CI pipelines)", 403)
    actor = str(claims["actor"])
    try:
        actor_id = int(claims["actor_id"])
    except (TypeError, ValueError):
        raise OIDCError("the identity token's actor_id isn't a GitHub account id")
    # The preview's owner is whoever ran the workflow; a bot or an account
    # Ephemera hasn't seen is recorded like a PR author is.
    owner = user_crud.get_or_create_user(db=db, github_id=actor_id, github_login=actor)
    raw = APIToken.generate_token()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.oidc_token_minutes)
    db.add(APIToken(user_id=owner.id, token_hash=APIToken.hash_token(raw), token_prefix=raw[:8],
                    token_type=TOKEN_TYPE, scope="previews", repository_full_name=installed.full_name,
                    name=f"GitHub Actions run {claims.get('run_id', '?')}",
                    description=f"OIDC: {claims.get('workflow_ref') or claims.get('job_workflow_ref') or ''}",
                    expires_at=expires, is_active=True))
    db.commit()
    logger.info(f"OIDC: {claimed} run {claims.get('run_id')} by {actor} got a pipeline token")
    return Exchanged(token=raw, repository=installed.full_name, expires_at=expires, actor=actor)


def _installed(full_name: str):
    from app.services.github import github_service

    for repo in github_service.list_installed_repositories():
        if repo.full_name.lower() == full_name.lower():
            return repo
    return None


def is_oidc(token: APIToken) -> bool:
    return token is not None and token.token_type == TOKEN_TYPE

