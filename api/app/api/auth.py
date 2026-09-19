"""
Authentication endpoints for GitHub OAuth
"""

import html
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import User
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

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Login Successful</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex; justify-content: center; align-items: center;
            height: 100vh; margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }}
        .container {{
            background: white; padding: 40px; border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1); text-align: center;
        }}
        h1 {{ color: #333; margin-bottom: 10px; }}
        p {{ color: #666; margin-bottom: 20px; }}
        .spinner {{
            border: 3px solid #f3f3f3; border-top: 3px solid #667eea; border-radius: 50%;
            width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 20px auto;
        }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
    </style>
</head>
<body>
    <div class="container">
        <h1>&#10003; Login Successful</h1>
        <p>Welcome, {html.escape(str(github_user['login']))}!</p>
        <div class="spinner"></div>
        <p>Redirecting to dashboard...</p>
    </div>
    <script>
        localStorage.setItem('ephemera_token', {_js_string(session_token)});
        setTimeout(() => {{ window.location.href = '/dashboard'; }}, 1500);
    </script>
</body>
</html>
"""
    response = HTMLResponse(content=html_content)
    response.delete_cookie(STATE_COOKIE)
    return response


def _js_string(value: str) -> str:
    """Serialize a string as a JavaScript literal safe for inline <script>."""
    import json
    return json.dumps(value).replace("<", "\\u003c")


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
        "created_at": current_user.created_at,
    }
