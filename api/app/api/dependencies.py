"""
API dependencies for authentication and authorization
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import APIToken, User


def _as_utc(dt: datetime) -> datetime:
    """Treat naive timestamps (SQLite, older rows) as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticate_token(db: Session, authorization: Optional[str]) -> Tuple[User, APIToken]:
    """
    Resolve an Authorization header of the form ``Bearer eph_...`` to (User, APIToken).

    Raises HTTPException(401) on any failure.
    """
    if not authorization:
        raise _unauthorized("Authorization header missing")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _unauthorized("Invalid authorization header format. Expected: Bearer <token>")

    token_value = parts[1]
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

    user = db.query(User).filter(User.id == token.user_id).first()
    if not user or not user.is_active:
        raise _unauthorized("User not found or inactive")

    token.last_used_at = now
    db.commit()

    return user, token


async def get_current_token(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> APIToken:
    """The APIToken that authenticated this request."""
    return authenticate_token(db, authorization)[1]


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """Get current authenticated user from Bearer token."""
    return authenticate_token(db, authorization)[0]


async def require_api_token(token: APIToken = Depends(get_current_token)) -> APIToken:
    """
    Only user-created API tokens may pass. Dashboard session tokens live in
    the browser (localStorage) and must not be able to export cloud secrets.
    """
    if token.token_type != APIToken.TYPE_API:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires an API token created from the dashboard, not a login session.",
        )
    return token


async def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Get current user if a valid token is provided, otherwise None."""
    if not authorization:
        return None
    try:
        return authenticate_token(db, authorization)[0]
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
