"""
Authentication endpoints for GitHub OAuth
"""

import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.dependencies import SESSION_COOKIE, get_current_token, get_current_user, is_admin
from app.config import get_settings
from app.database import get_db
from app.models import APIToken, User
from app.services.auth import GitHubOAuthService, get_github_oauth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "ephemera_oauth_state"


@router.get("/github/login")
async def github_login(
    oauth_service: GitHubOAuthService = Depends(get_github_oauth_service),
):
    """
    Initiate GitHub OAuth login flow.

    Generates a CSRF ``state`` value, stores it in a short-lived HttpOnly
    cookie, and redirects the user to GitHub.
    """
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(url=oauth_service.get_authorization_url(state=state))
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=get_settings().environment != "development",
    )
    return response


@router.get("/github/callback")
async def github_callback(
    code: str,
    state: Optional[str] = None,
    oauth_state: Optional[str] = Cookie(None, alias=STATE_COOKIE),
    db: Session = Depends(get_db),
    oauth_service: GitHubOAuthService = Depends(get_github_oauth_service),
):
    """
    Handle GitHub OAuth callback.

    Verifies the CSRF state, exchanges the code for a GitHub token, upserts
    the user, and hands the browser a dashboard session token.
    """
    if not state or not oauth_state or not secrets.compare_digest(state, oauth_state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state. Please start the login again.",
        )

    try:
        github_token = await oauth_service.exchange_code_for_token(code)
        github_user = await oauth_service.get_github_user(github_token)
        user = oauth_service.create_or_update_user(db, github_user)
        session_token = oauth_service.create_session_token(db, user)
    except ValueError as e:
        logger.error(f"GitHub OAuth error: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception(f"Unexpected error during GitHub OAuth: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed",
        )

    logger.info(f"User {user.github_login} authenticated successfully")

    # The session lives in an HttpOnly cookie. Page scripts cannot read it,
    # so a cross-site scripting bug in the dashboard cannot steal the session
    # the way it could when the token sat in localStorage.
    settings = get_settings()
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=settings.session_token_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.environment != "development",
        path="/",
    )
    response.delete_cookie(STATE_COOKIE)
    return response


@router.post("/logout")
async def logout(
    response: Response,
    token: APIToken = Depends(get_current_token),
    db: Session = Depends(get_db),
):
    """
    End the dashboard session: revoke the session token and clear the cookie.

    API tokens are not revoked here; use the tokens API for those.
    """
    if token.token_type == APIToken.TYPE_SESSION:
        token.is_active = False
        token.revoked_at = datetime.now(timezone.utc)
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get the authenticated user's profile. Requires a Bearer token."""
    return {
        "id": current_user.id,
        "github_id": current_user.github_id,
        "github_login": current_user.github_login,
        "email": current_user.email,
        "avatar_url": current_user.avatar_url,
        "is_active": current_user.is_active,
        "is_admin": is_admin(current_user),
        "created_at": current_user.created_at,
    }
