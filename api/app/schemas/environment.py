from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from app.models.environment import EnvironmentStatus


class EnvironmentCreate(BaseModel):
    """Schema for creating a new environment via API"""
    repository_full_name: str
    repository_name: str
    pr_number: int
    pr_title: str
    branch_name: str
    commit_sha: str
    installation_id: int
    user_id: int
    user_login: str
    user_avatar_url: Optional[str] = None


class EnvironmentResponse(BaseModel):
    id: int
    repository_full_name: str
    repository_name: str
    pr_number: int
    pr_title: Optional[str]
    branch_name: str
    commit_sha: str
    namespace: str
    environment_url: Optional[str]
    status: EnvironmentStatus
    installation_id: int
    owner_id: int
    error_message: Optional[str]
    last_deployed_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]
    destroyed_at: Optional[datetime]

    class Config:
        from_attributes = True
