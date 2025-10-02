from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.crud import environment as environment_crud
from app.schemas.environment import EnvironmentResponse

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
