import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.crud import environment as environment_crud
from app.crud import user as user_crud
from app.database import get_db
from app.models import Environment, User
from app.schemas.environment import EnvironmentCreate, EnvironmentResponse
from app.services.provisioning import EnvironmentRequest, request_environment

logger = logging.getLogger(__name__)

# Every route here requires a Bearer token: environments are provisioned on a
# shared cluster and the listing exposes repository names and PR titles.
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/", response_model=List[EnvironmentResponse])
async def list_environments(
    db: Session = Depends(get_db),
    repository: Optional[str] = None,
    active_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
):
    """List environments, newest first."""
    if active_only:
        return environment_crud.get_active_environments(db)
    if repository:
        return environment_crud.get_environments_by_repo(db, repository)
    return (
        db.query(Environment)
        .order_by(Environment.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/{environment_id}", response_model=EnvironmentResponse)
async def get_environment(environment_id: int, db: Session = Depends(get_db)):
    """Get environment by ID"""
    environment = environment_crud.get_environment_by_id(db, environment_id)
    if not environment:
        raise HTTPException(status_code=404, detail="Environment not found")
    return environment


@router.get("/namespace/{namespace}", response_model=EnvironmentResponse)
async def get_environment_by_namespace(namespace: str, db: Session = Depends(get_db)):
    """Get environment by namespace"""
    environment = environment_crud.get_environment_by_namespace(db, namespace)
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
    """
    logger.info(
        f"{current_user.github_login} requested environment for "
        f"PR #{env_data.pr_number} in {env_data.repository_full_name}"
    )

    owner = user_crud.get_or_create_user(
        db=db,
        github_id=env_data.user_id,
        github_login=env_data.user_login,
        avatar_url=env_data.user_avatar_url,
    )

    environment, action = request_environment(
        db,
        EnvironmentRequest(
            repository_full_name=env_data.repository_full_name,
            repository_name=env_data.repository_name,
            pr_number=env_data.pr_number,
            pr_title=env_data.pr_title,
            branch_name=env_data.branch_name,
            commit_sha=env_data.commit_sha,
            installation_id=env_data.installation_id,
            owner=owner,
        ),
    )
    logger.info(f"Environment {environment.namespace}: {action}")
    return environment
