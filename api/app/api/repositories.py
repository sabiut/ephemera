"""
Repositories the caller can work with: the ones the Ephemera GitHub App is
installed on and the caller collaborates on (all of them for admins).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, is_admin
from app.crud import environment as environment_crud
from app.database import get_db
from app.models import User
from app.services import repo_access, setup_check
from app.services.github import GitHubUnavailable, InstalledRepository, github_service

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
async def list_repositories(refresh: bool = False, current_user: User = Depends(get_current_user)):
    """
    Repositories with the App installed that the caller can see previews for.
    ``refresh=true`` asks GitHub again instead of using cached access (the
    dashboard's Refresh repositories button).
    """
    if refresh:
        repo_access.invalidate()
    try:
        repos = repo_access.accessible_repositories(current_user, is_admin(current_user))
    except GitHubUnavailable:
        raise HTTPException(status_code=503, detail="GitHub App integration is not configured on this server")
    return RepositoryListResponse(
        repositories=[RepositoryResponse(**r.__dict__) for r in repos],
        install_url=github_service.app_install_url(),
    )


def _accessible_repo(owner: str, repo: str, user: User) -> InstalledRepository:
    """The named repository if the caller can see it, else 404."""
    full_name = f"{owner}/{repo}".lower()
    try:
        repos = repo_access.accessible_repositories(user, is_admin(user))
    except GitHubUnavailable:
        raise HTTPException(status_code=503, detail="GitHub App integration is not configured on this server")
    for r in repos:
        if r.full_name.lower() == full_name:
            return r
    raise HTTPException(status_code=404, detail="Repository not found, or the Ephemera App is not installed on it")


@router.get("/repositories/{owner}/{repo}/check")
async def check_repository(owner: str, repo: str, pr: Optional[int] = None,
                           current_user: User = Depends(get_current_user)):
    """
    Check the repository's compose file: on the default branch, or with
    ``pr=N`` at that pull request's latest commit, so a fix made inside the
    PR is what gets checked.
    """
    installed = _accessible_repo(owner, repo, current_user)
    ref, label = None, None
    if pr is not None:
        try:
            pull = github_service.get_pull_request(installed.installation_id, installed.full_name, pr)
        except GitHubUnavailable:
            raise HTTPException(status_code=503, detail="GitHub App integration is not configured on this server")
        if pull is None:
            raise HTTPException(status_code=404, detail=f"Pull request #{pr} not found in {installed.full_name}")
        ref, label = pull.head_sha, f"PR #{pr} ({pull.head_sha[:7]})"
    report = setup_check.check_repository(installed, ref=ref)
    body = report.as_dict()
    body["ref_label"] = label or report.ref
    body["pr_number"] = pr
    return body


class PullResponse(BaseModel):
    number: int
    title: str
    author_login: str
    head_sha: str
    environment_id: Optional[int] = None
    environment_status: Optional[str] = None
    environment_url: Optional[str] = None


@router.get("/repositories/{owner}/{repo}/pulls", response_model=List[PullResponse])
async def list_pulls(owner: str, repo: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Open pull requests with the state of each one's preview."""
    installed = _accessible_repo(owner, repo, current_user)
    try:
        pulls = github_service.list_open_pulls(installed.installation_id, installed.full_name)
    except GitHubUnavailable:
        raise HTTPException(status_code=503, detail="GitHub App integration is not configured on this server")
    out = []
    for pr in pulls:
        env = environment_crud.get_environment_by_pr(db, installed.full_name, pr.number)
        out.append(PullResponse(
            number=pr.number, title=pr.title, author_login=pr.author_login, head_sha=pr.head_sha,
            environment_id=env.id if env else None,
            environment_status=env.status.value.lower() if env else None,
            environment_url=env.environment_url if env else None,
        ))
    return out
