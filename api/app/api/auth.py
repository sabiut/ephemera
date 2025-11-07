"""
Authentication endpoints for GitHub OAuth
"""

from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
import logging
import secrets

from app.database import get_db
from app.schemas.auth import GitHubOAuthCallback, AuthResponse
from app.services.auth import get_github_oauth_service, GitHubOAuthService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/github/login")
async def github_login(
    oauth_service: GitHubOAuthService = Depends(get_github_oauth_service)
):
    """
    Initiate GitHub OAuth login flow.

    Redirects user to GitHub authorization page.
    """
    # Generate CSRF token
    state = secrets.token_urlsafe(32)

    # Get authorization URL
    auth_url = oauth_service.get_authorization_url(state=state)

    logger.info(f"Redirecting to GitHub OAuth: {auth_url}")

    return RedirectResponse(url=auth_url)


@router.get("/github/callback")
async def github_callback(
    code: str,
    state: str = None,
    db: Session = Depends(get_db),
    oauth_service: GitHubOAuthService = Depends(get_github_oauth_service)
):
    """
    Handle GitHub OAuth callback.

    Exchanges code for access token, creates/updates user, and returns session token.
    """
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authorization code is required"
        )

    try:
        # Exchange code for GitHub access token
        github_token = await oauth_service.exchange_code_for_token(code)

        # Get GitHub user info
        github_user = await oauth_service.get_github_user(github_token)

        # Create or update user in our database
        user = oauth_service.create_or_update_user(db, github_user)

        # Create session token for web UI
        session_token = oauth_service.create_session_token(db, user)

        logger.info(f"User {user.github_login} authenticated successfully")

        # Return HTML that stores token and redirects to dashboard
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Login Successful</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                }}
                .container {{
                    background: white;
                    padding: 40px;
                    border-radius: 10px;
                    box-shadow: 0 10px 40px rgba(0,0,0,0.1);
                    text-align: center;
                }}
                h1 {{ color: #333; margin-bottom: 10px; }}
                p {{ color: #666; margin-bottom: 20px; }}
                .spinner {{
                    border: 3px solid #f3f3f3;
                    border-top: 3px solid #667eea;
                    border-radius: 50%;
                    width: 40px;
                    height: 40px;
                    animation: spin 1s linear infinite;
                    margin: 20px auto;
                }}
                @keyframes spin {{
                    0% {{ transform: rotate(0deg); }}
                    100% {{ transform: rotate(360deg); }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>✓ Login Successful</h1>
                <p>Welcome, {github_user['login']}!</p>
                <div class="spinner"></div>
                <p>Redirecting to dashboard...</p>
            </div>
            <script>
                // Store token in localStorage
                localStorage.setItem('ephemera_token', '{session_token.token}');

                // Redirect to dashboard
                setTimeout(() => {{
                    window.location.href = '/dashboard';
                }}, 2000);
            </script>
        </body>
        </html>
        """

        return HTMLResponse(content=html_content)

    except ValueError as e:
        logger.error(f"GitHub OAuth error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error during GitHub OAuth: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed"
        )


@router.get("/me")
async def get_current_user_info(
    db: Session = Depends(get_db)
):
    """
    Get current authenticated user information.

    Requires Bearer token in Authorization header.
    """
    from app.api.dependencies import get_current_user
    from fastapi import Depends as FastAPIDepends

    user = await get_current_user(db=db)

    return {
        "id": user.id,
        "github_id": user.github_id,
        "github_login": user.github_login,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }
