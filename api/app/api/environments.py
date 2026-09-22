import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, is_admin
from app.crud import environment as environment_crud
from app.crud import user as user_crud
from app.database import get_db
from app.models import User
from app.schemas.environment import EnvironmentCreate, EnvironmentResponse
from app.services import repo_access
from app.services.github import GitHubUnavailable, github_service
from app.services.provisioning import EnvironmentRequest, request_environment

logger = logging.getLogger(__name__)

# Every route here requires a Bearer token: environments are provisioned on a
# shared cluster and the listing exposes repository names and PR titles.
#
# Reads are scoped to the caller. A user sees environments for PRs they
# authored and for every repository where GitHub lists them as a
# collaborator; logins in ADMIN_GITHUB_LOGINS see everything. A lookup of an
# environment outside that scope is a 404, not a 403, so ids and namespaces
# cannot be probed for existence.
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/", response_model=List[EnvironmentResponse])
async def list_environments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    repository: Optional[str] = None,
    active_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
):
    """List the caller's visible environments, newest first."""
    admin = is_admin(current_user)
    return environment_crud.list_environments(
        db,
        current_user,
        admin,
        repository=repository,
        active_only=active_only,
        limit=limit,
        repo_names=None if admin else repo_access.accessible_repo_names(current_user, admin),
    )


@router.get("/{environment_id}", response_model=EnvironmentResponse)
async def get_environment(
    environment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get one of the caller's visible environments by ID"""
    admin = is_admin(current_user)
    environment = environment_crud.get_visible_environment(
        db, current_user, admin, environment_id=environment_id,
        repo_names=None if admin else repo_access.accessible_repo_names(current_user, admin),
    )
    if not environment:
        raise HTTPException(status_code=404, detail="Environment not found")
    return environment


@router.get("/namespace/{namespace}", response_model=EnvironmentResponse)
async def get_environment_by_namespace(
    namespace: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get one of the caller's visible environments by namespace"""
    admin = is_admin(current_user)
    environment = environment_crud.get_visible_environment(
        db, current_user, admin, namespace=namespace,
        repo_names=None if admin else repo_access.accessible_repo_names(current_user, admin),
    )
    if not environment:
        raise HTTPException(status_code=404, detail="Environment not found")
    return environment


@router.post("/", response_model=EnvironmentResponse, status_code=202)
async def create_environment(
    env_data: EnvironmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create (or re-provision) a preview environment for a PR.

    Called by GitHub Actions workflows with a Bearer token. If a live
    environment already exists for the PR it is returned unchanged.

    Nothing about the repository is taken on trust from the caller. The
    installation is the one GitHub reports for the repository, the PR must
    exist there, its author becomes the owner, and the caller must be an
    admin, the PR author, or a collaborator on the repository.
    """
    repo = env_data.repository_full_name
    logger.info(f"{current_user.github_login} requested environment for PR #{env_data.pr_number} in {repo}")

    try:
        installation_id = github_service.get_repo_installation_id(repo)
        if installation_id is None:
            raise HTTPException(status_code=404, detail=f"The Ephemera GitHub App is not installed on {repo}")
        if env_data.installation_id is not None and env_data.installation_id != installation_id:
            raise HTTPException(
                status_code=400,
                detail=f"installation_id {env_data.installation_id} does not own {repo}",
            )

        pr = github_service.get_pull_request(installation_id, repo, env_data.pr_number)
        if pr is None:
            raise HTTPException(status_code=404, detail=f"Pull request #{env_data.pr_number} not found in {repo}")

        if not _may_provision(installation_id, repo, current_user, pr.author_id):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"{current_user.github_login} is not a collaborator on {repo}. Only the PR author, "
                    "a repository collaborator, or an admin (ADMIN_GITHUB_LOGINS) can create its environments."
                ),
            )
    except GitHubUnavailable:
        raise HTTPException(status_code=503, detail="GitHub App integration is not configured on this server")

    owner = user_crud.get_or_create_user(
        db=db,
        github_id=pr.author_id,
        github_login=pr.author_login,
        avatar_url=pr.author_avatar_url,
    )

    environment, action = request_environment(
        db,
        EnvironmentRequest(
            repository_full_name=repo,
            repository_name=env_data.repository_name or repo.rpartition("/")[2],
            pr_number=pr.number,
            pr_title=env_data.pr_title or pr.title,
            branch_name=env_data.branch_name or pr.head_ref,
            commit_sha=env_data.commit_sha or pr.head_sha,
            installation_id=installation_id,
            owner=owner,
        ),
    )
    logger.info(f"Environment {environment.namespace}: {action}")
    return environment


def _may_provision(installation_id: int, repo: str, caller: User, pr_author_id: int) -> bool:
    """Admins, the PR author, and repository collaborators may provision."""
    if is_admin(caller) or caller.github_id == pr_author_id:
        return True
    # None means the check itself failed (for example the App lacks the
    # Metadata permission). Fail closed: an unverifiable caller is denied.
    return github_service.is_collaborator(installation_id, repo, caller.github_login) is True
