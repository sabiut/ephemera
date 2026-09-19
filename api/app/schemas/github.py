from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime


class GitHubRepository(BaseModel):
    id: int
    name: str
    full_name: str
    private: bool = False
    html_url: Optional[str] = None
    clone_url: Optional[str] = None
    default_branch: Optional[str] = None


class GitHubUser(BaseModel):
    id: int
    login: str
    avatar_url: Optional[str] = None
    html_url: Optional[str] = None


class GitHubPullRequest(BaseModel):
    id: int
    number: int
    title: str
    state: str
    html_url: Optional[str] = None
    head: Dict[str, Any]  # Contains ref (branch name), sha, repo
    base: Dict[str, Any]  # Contains ref (target branch), sha, repo
    user: GitHubUser
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    merged: Optional[bool] = False
    draft: Optional[bool] = False


class PullRequestWebhook(BaseModel):
    action: str  # "opened", "closed", "reopened", "synchronize", etc.
    number: int
    pull_request: GitHubPullRequest
    repository: GitHubRepository
    sender: GitHubUser
    installation: Optional[Dict[str, Any]] = None


class WebhookEvent(BaseModel):
    """Generic webhook event wrapper"""
    event_type: str = Field(..., description="GitHub event type (e.g., 'pull_request')")
    delivery_id: str = Field(..., description="Unique delivery ID from GitHub")
    payload: Dict[str, Any] = Field(..., description="Raw webhook payload")
