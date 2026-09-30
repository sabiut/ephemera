from pydantic import BaseModel, ConfigDict, Field, computed_field
from datetime import datetime
from typing import Any, Dict, Optional
from app.models.deployment import DeploymentStatus
from app.models.environment import EnvironmentStatus
from app.services import lifecycle
from app.services.diagnosis import explain

# Statuses whose preview is still running (or failing) and so can expire.
_HOLDS_RESOURCES = (EnvironmentStatus.PENDING, EnvironmentStatus.PROVISIONING, EnvironmentStatus.READY,
                    EnvironmentStatus.UPDATING, EnvironmentStatus.FAILED)


class EnvironmentCreate(BaseModel):
    """
    Request body for creating a preview environment via the API.

    Only the repository and PR number are required. The server looks the PR
    up through the GitHub App and takes the installation, the PR author (who
    becomes the owner) and, unless supplied, the title, branch and head
    commit from GitHub rather than from the caller.

    ``installation_id``, ``user_id``, ``user_login`` and ``user_avatar_url``
    are accepted for backwards compatibility. The installation id must match
    the one GitHub reports for the repository; the user fields are ignored.
    """
    repository_full_name: str
    pr_number: int
    repository_name: Optional[str] = None
    pr_title: Optional[str] = None
    branch_name: Optional[str] = None
    commit_sha: Optional[str] = None
    installation_id: Optional[int] = None
    user_id: Optional[int] = None
    user_login: Optional[str] = None
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
    service_urls: Optional[Dict[str, str]] = None
    status: EnvironmentStatus
    installation_id: int
    owner_id: int
    owner_login: Optional[str] = None
    error_message: Optional[str]
    last_deployed_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]
    destroyed_at: Optional[datetime]
    removal_reason: Optional[str] = None
    kept_at: Optional[datetime] = None
    access_applied: Optional[str] = None
    readiness: Optional[Dict[str, Any]] = None
    stage: Optional[str] = None
    stage_detail: Optional[str] = None
    stage_started_at: Optional[datetime] = None
    deploy_started_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def expires_at(self) -> Optional[datetime]:
        """When this preview will be removed if nothing happens, or None."""
        if self.status not in _HOLDS_RESOURCES:
            return None
        return lifecycle.expires_at(self)

    @computed_field
    @property
    def diagnosis(self) -> Optional[Dict[str, Any]]:
        """For a failed preview: what happened and what to do, ahead of the raw error."""
        if self.status != EnvironmentStatus.FAILED:
            return None
        return explain(self.error_message, self.repository_full_name, self.commit_sha, self.pr_number).as_dict()


class DeploymentResponse(BaseModel):
    """One attempt to deploy a commit to a preview."""
    id: int
    commit_sha: str
    status: DeploymentStatus
    error_message: Optional[str]
    ai_generated: bool = False
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class BuildResponse(BaseModel):
    """One managed build of a preview's commit (docs/managed-builds.md)."""
    id: int
    commit_sha: str
    status: str                                  # queued, building, succeeded, failed, timeout, cancelled
    services: Optional[Dict[str, str]] = None    # service -> queued, building, done, failed, cancelled
    images: Optional[Dict[str, str]] = None
    failure_category: Optional[str] = None
    log_tail: Optional[str] = None
    duration_seconds: Optional[int] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # failure_detail is left out on purpose: it holds Google's raw errors.
    log_object: Optional[str] = Field(default=None, exclude=True)

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def has_log(self) -> bool:
        """The whole log can be downloaded (GET .../builds/{id}/log)."""
        return bool(self.log_object)
