"""
GitHub OAuth authentication service
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crud import user as user_crud
from app.models import APIToken, User


class GitHubOAuthService:
    """Handle GitHub OAuth flow"""

    def __init__(self):
        settings = get_settings()
        self.client_id = settings.github_oauth_client_id
        self.client_secret = settings.github_oauth_client_secret
        self.redirect_uri = settings.github_oauth_redirect_uri
        self.session_ttl = timedelta(days=settings.session_token_ttl_days)

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET must be set. "
                "Create a GitHub OAuth App at: https://github.com/settings/developers"
            )

    def get_authorization_url(self, state: str) -> str:
        """Build the GitHub authorization URL. ``state`` is the CSRF token."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "read:user user:email",
            "state": state,
        }
        return f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str) -> str:
        """Exchange an authorization code for a GitHub access token."""
        async with httpx.AsyncClient(timeout=15.0) as client:
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
        """Fetch the GitHub user profile, filling in the primary email if hidden."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github.v3+json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get("https://api.github.com/user", headers=headers)
            response.raise_for_status()
            user_data = response.json()

            if not user_data.get("email"):
                email_response = await client.get("https://api.github.com/user/emails", headers=headers)
                if email_response.status_code == 200:
                    emails = email_response.json()
                    user_data["email"] = next(
                        (e["email"] for e in emails if e.get("primary")),
                        emails[0]["email"] if emails else None,
                    )

            return user_data

    def create_or_update_user(self, db: Session, github_user: Dict) -> User:
        """Create or update the local user from GitHub profile data."""
        return user_crud.get_or_create_user(
            db=db,
            github_id=github_user["id"],
            github_login=github_user["login"],
            email=github_user.get("email"),
            avatar_url=github_user.get("avatar_url"),
        )

    def create_session_token(self, db: Session, user: User) -> str:
        """
        Create an expiring session token for the web dashboard.

        Returns the raw token. Only its hash is stored.
        """
        token = APIToken.generate_token()

        db_token = APIToken(
            user_id=user.id,
            token_hash=APIToken.hash_token(token),
            token_prefix=token[:8],
            token_type=APIToken.TYPE_SESSION,
            name="Web Dashboard Session",
            description="Auto-generated session token for web UI",
            is_active=True,
            expires_at=datetime.now(timezone.utc) + self.session_ttl,
        )
        db.add(db_token)
        db.commit()

        return token


# Global instance
_github_oauth_service: Optional[GitHubOAuthService] = None


def get_github_oauth_service() -> GitHubOAuthService:
    """Get or create global GitHub OAuth service instance"""
    global _github_oauth_service
    if _github_oauth_service is None:
        _github_oauth_service = GitHubOAuthService()
    return _github_oauth_service
