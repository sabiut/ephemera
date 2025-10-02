from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime


class GitHubRepository(BaseModel):
    id: int
    name: str
    full_name: str
    private: bool
    html_url: str
    clone_url: str
    default_branch: str


class GitHubUser(BaseModel):
    id: int
    login: str
    avatar_url: str
    html_url: str


class GitHubPullRequest(BaseModel):
    id: int
    number: int
    title: str
    state: str
    html_url: str
    head: Dict[str, Any]  # Contains ref (branch name), sha, repo
    base: Dict[str, Any]  # Contains ref (target branch), sha, repo
    user: GitHubUser
    created_at: datetime
    updated_at: datetime
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
