"""
API dependencies for authentication and authorization
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import APIToken, User


SESSION_COOKIE = "ephemera_session"
CSRF_HEADER = "x-requested-with"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _as_utc(dt: datetime) -> datetime:
    """Treat naive timestamps (SQLite, older rows) as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve_token(db: Session, token_value: str) -> APIToken:
    """Look up a raw ``eph_...`` token and check it is live. Raises 401 otherwise."""
    if not token_value.startswith("eph_"):
        raise _unauthorized("Invalid token format")

    token = (
        db.query(APIToken)
        .filter(APIToken.token_hash == APIToken.hash_token(token_value))
        .first()
    )
    if not token:
        raise _unauthorized("Invalid token")

    if not token.is_active or token.revoked_at:
        raise _unauthorized("Token has been revoked")

    now = datetime.now(timezone.utc)
    if token.expires_at and _as_utc(token.expires_at) < now:
        raise _unauthorized("Token has expired")

    return token


def authenticate_token(
    db: Session,
    authorization: Optional[str],
    session_cookie: Optional[str] = None,
    method: str = "GET",
    requested_with: Optional[str] = None,
) -> Tuple[User, APIToken]:
    """
    Resolve the request's credentials to (User, APIToken).

    Two credentials are accepted:

    * ``Authorization: Bearer eph_...`` for API tokens and any programmatic
      caller. The header is never sent by a browser on its own, so it needs
      no CSRF protection.
    * The ``ephemera_session`` cookie set by the OAuth callback. It is
      HttpOnly so page scripts cannot read it, which means it also rides
      along on cross-site requests the user did not intend. Only session
      tokens are accepted this way, and for anything other than a safe
      method the request must carry an ``X-Requested-With`` header. A
      cross-origin page cannot add that header without a CORS preflight
      this API does not grant, so the cookie alone cannot mutate anything.

    Raises HTTPException(401) on any failure, 403 for a cookie-authenticated
    mutation without the header.
    """
    if authorization:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise _unauthorized("Invalid authorization header format. Expected: Bearer <token>")
        token = _resolve_token(db, parts[1])
    elif session_cookie:
        token = _resolve_token(db, session_cookie)
        if token.token_type != APIToken.TYPE_SESSION:
            raise _unauthorized("Only dashboard sessions may authenticate with a cookie")
        if method.upper() not in SAFE_METHODS and not requested_with:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cookie-authenticated requests must include an X-Requested-With header",
            )
    else:
        raise _unauthorized("Authorization header missing")

    now = datetime.now(timezone.utc)
    user = db.query(User).filter(User.id == token.user_id).first()
    if not user or not user.is_active:
        raise _unauthorized("User not found or inactive")

    token.last_used_at = now
    db.commit()

    return user, token


def _authenticate_request(request: Request, db: Session) -> Tuple[User, APIToken]:
    return authenticate_token(
        db,
        request.headers.get("authorization"),
        session_cookie=request.cookies.get(SESSION_COOKIE),
        method=request.method,
        requested_with=request.headers.get(CSRF_HEADER),
    )


async def get_current_token(request: Request, db: Session = Depends(get_db)) -> APIToken:
    """The APIToken that authenticated this request."""
    return _authenticate_request(request, db)[1]


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Get the current authenticated user from the Bearer token or session cookie."""
    return _authenticate_request(request, db)[0]


async def require_api_token(token: APIToken = Depends(get_current_token)) -> APIToken:
    """
    Only user-created API tokens may pass. Dashboard sessions belong to a
    browser, which is a far easier place to hijack than a CI secret store,
    so they must not be able to export cloud secrets.
    """
    if token.token_type != APIToken.TYPE_API:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires an API token created from the dashboard, not a login session.",
        )
    return token


async def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Get current user if valid credentials are provided, otherwise None."""
    if not request.headers.get("authorization") and not request.cookies.get(SESSION_COOKIE):
        return None
    try:
        return _authenticate_request(request, db)[0]
    except HTTPException:
        return None


def is_admin(user: User) -> bool:
    """
    Admins see every environment; everyone else sees only their own.

    Membership is configured with ADMIN_GITHUB_LOGINS rather than stored on
    the user row, so granting or revoking it is a config change, not a
    migration or a database edit.
    """
    return user.github_login.lower() in settings.admin_login_set
