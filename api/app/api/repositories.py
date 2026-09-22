"""
Repositories the caller can work with: the ones the Ephemera GitHub App is
installed on and the caller collaborates on (all of them for admins).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import get_current_user, is_admin
from app.models import User
from app.services import repo_access
from app.services.github import GitHubUnavailable, github_service

router = APIRouter(dependencies=[Depends(get_current_user)])


class RepositoryResponse(BaseModel):
    full_name: str
    name: str
    installation_id: int
    private: bool
    default_branch: str
    html_url: str


class RepositoryListResponse(BaseModel):
    repositories: List[RepositoryResponse]
    install_url: Optional[str] = None


@router.get("/repositories", response_model=RepositoryListResponse)
async def list_repositories(current_user: User = Depends(get_current_user)):
    """Repositories with the App installed that the caller can see previews for."""
    try:
        repos = repo_access.accessible_repositories(current_user, is_admin(current_user))
    except GitHubUnavailable:
        raise HTTPException(status_code=503, detail="GitHub App integration is not configured on this server")
    return RepositoryListResponse(
        repositories=[RepositoryResponse(**r.__dict__) for r in repos],
        install_url=github_service.app_install_url(),
    )
