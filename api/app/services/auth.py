"""
GitHub OAuth authentication service
"""

import os
import httpx
from typing import Optional, Dict
from sqlalchemy.orm import Session

from app.models import User, APIToken
from app.crud import user as user_crud


class GitHubOAuthService:
    """Handle GitHub OAuth flow"""

    def __init__(self):
        self.client_id = os.getenv("GITHUB_OAUTH_CLIENT_ID")
        self.client_secret = os.getenv("GITHUB_OAUTH_CLIENT_SECRET")
        self.redirect_uri = os.getenv("GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/github/callback")

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET must be set. "
                "Create a GitHub OAuth App at: https://github.com/settings/developers"
            )

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """
        Get GitHub OAuth authorization URL.

        Args:
            state: Optional state parameter for CSRF protection

        Returns:
            GitHub authorization URL
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "read:user user:email",
        }

        if state:
            params["state"] = state

        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"https://github.com/login/oauth/authorize?{query_string}"

    async def exchange_code_for_token(self, code: str) -> str:
        """
        Exchange authorization code for access token.

        Args:
            code: Authorization code from GitHub

        Returns:
            GitHub access token
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
            )

            response.raise_for_status()
            data = response.json()

            if "error" in data:
                raise ValueError(f"GitHub OAuth error: {data.get('error_description', data['error'])}")

            return data["access_token"]

    async def get_github_user(self, access_token: str) -> Dict:
        """
        Get GitHub user information using access token.

        Args:
            access_token: GitHub access token

        Returns:
            GitHub user data
        """
        async with httpx.AsyncClient() as client:
            # Get user info
            response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )

            response.raise_for_status()
            user_data = response.json()

            # Get user emails if not in profile
            if not user_data.get("email"):
                email_response = await client.get(
                    "https://api.github.com/user/emails",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )

                if email_response.status_code == 200:
                    emails = email_response.json()
                    # Get primary email
                    primary_email = next(
                        (e["email"] for e in emails if e.get("primary")),
                        emails[0]["email"] if emails else None,
                    )
                    user_data["email"] = primary_email

            return user_data

    def create_or_update_user(self, db: Session, github_user: Dict) -> User:
        """
        Create or update user from GitHub data.

        Args:
            db: Database session
            github_user: GitHub user data

        Returns:
            User model instance
        """
        return user_crud.get_or_create_user(
            db=db,
            github_id=github_user["id"],
            github_login=github_user["login"],
            email=github_user.get("email"),
            avatar_url=github_user.get("avatar_url"),
        )

    def create_session_token(self, db: Session, user: User) -> APIToken:
        """
        Create a session token for web UI.

        Args:
            db: Database session
            user: User instance

        Returns:
            APIToken instance
        """
        token = APIToken.generate_token()
        token_prefix = token[:8]

        db_token = APIToken(
            user_id=user.id,
            token=token,
            token_prefix=token_prefix,
            name="Web Dashboard Session",
            description="Auto-generated session token for web UI",
            is_active=True,
        )

        db.add(db_token)
        db.commit()
        db.refresh(db_token)

        # Attach full token for return
        db_token.token = token
        return db_token


# Global instance
_github_oauth_service = None


def get_github_oauth_service() -> GitHubOAuthService:
    """Get or create global GitHub OAuth service instance"""
    global _github_oauth_service
    if _github_oauth_service is None:
        _github_oauth_service = GitHubOAuthService()
    return _github_oauth_service
