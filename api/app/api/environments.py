from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
import logging

from app.database import get_db
from app.crud import environment as environment_crud
from app.crud import user as user_crud
from app.crud import deployment as deployment_crud
from app.schemas.environment import EnvironmentResponse, EnvironmentCreate
from app.services.github import github_service
from app.tasks.environment import provision_environment

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=List[EnvironmentResponse])
async def list_environments(
    db: Session = Depends(get_db),
    repository: str = None,
    active_only: bool = False
):
    """List all environments"""
    if active_only:
        environments = environment_crud.get_active_environments(db)
    elif repository:
        environments = environment_crud.get_environments_by_repo(db, repository)
    else:
        # Get all environments - limit to recent 100
        environments = db.query(environment_crud.Environment).order_by(
            environment_crud.Environment.created_at.desc()
        ).limit(100).all()

    return environments


@router.get("/{environment_id}", response_model=EnvironmentResponse)
async def get_environment(
    environment_id: int,
    db: Session = Depends(get_db)
):
    """Get environment by ID"""
    environment = environment_crud.get_environment_by_id(db, environment_id)
    if not environment:
        raise HTTPException(status_code=404, detail="Environment not found")
    return environment


@router.get("/namespace/{namespace}", response_model=EnvironmentResponse)
async def get_environment_by_namespace(
    namespace: str,
    db: Session = Depends(get_db)
):
    """Get environment by namespace"""
    environment = environment_crud.get_environment_by_namespace(db, namespace)
    if not environment:
        raise HTTPException(status_code=404, detail="Environment not found")
    return environment


@router.post("/", response_model=EnvironmentResponse)
async def create_environment(
    env_data: EnvironmentCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Create a new preview environment for a PR.
    Called by GitHub Actions workflow.
    """
    logger.info(f"Creating environment for PR #{env_data.pr_number} in {env_data.repository_full_name}")

    # Check if environment already exists
    existing_env = environment_crud.get_environment_by_pr(
        db, env_data.repository_full_name, env_data.pr_number
    )
    if existing_env:
        logger.info(f"Environment already exists for PR #{env_data.pr_number}")
        return existing_env

    # Get or create user
    owner = user_crud.get_or_create_user(
        db=db,
        github_id=env_data.user_id,
        github_login=env_data.user_login,
        avatar_url=env_data.user_avatar_url
    )

    # Build environment URL
    env_url = github_service.build_environment_url(
        env_data.pr_number,
        env_data.repository_name
    )

    # Create environment in database
    environment = environment_crud.create_environment(
        db=db,
        repository_full_name=env_data.repository_full_name,
        repository_name=env_data.repository_name,
        pr_number=env_data.pr_number,
        pr_title=env_data.pr_title,
        branch_name=env_data.branch_name,
        commit_sha=env_data.commit_sha,
        installation_id=env_data.installation_id,
        owner=owner,
        environment_url=env_url
    )

    logger.info(f"Created environment {environment.namespace} for PR #{env_data.pr_number}")

    # Create initial deployment
    deployment = deployment_crud.create_deployment(
        db=db,
        environment=environment,
        commit_sha=env_data.commit_sha
    )

    logger.info(f"Created deployment {deployment.id} for environment {environment.id}")

    # Queue environment provisioning task
    provision_environment.delay(
        environment_id=environment.id,
        installation_id=env_data.installation_id,
        repo_full_name=env_data.repository_full_name,
        pr_number=env_data.pr_number,
        commit_sha=env_data.commit_sha
    )

    return environment
