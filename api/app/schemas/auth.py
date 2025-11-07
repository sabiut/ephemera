from pydantic import BaseModel
from typing import Optional


class GitHubOAuthCallback(BaseModel):
    """Schema for GitHub OAuth callback"""
    code: str
    state: Optional[str] = None


class AuthResponse(BaseModel):
    """Schema for authentication response"""
    access_token: str
    token_type: str = "bearer"
    user: dict


class GitHubUser(BaseModel):
    """Schema for GitHub user info"""
    id: int
    login: str
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    name: Optional[str] = None
