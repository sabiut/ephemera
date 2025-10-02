from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
from app.models.deployment import Deployment, DeploymentStatus
from app.models.environment import Environment


def get_deployment_by_id(db: Session, deployment_id: int) -> Optional[Deployment]:
    """Get deployment by ID"""
    return db.query(Deployment).filter(Deployment.id == deployment_id).first()


def get_deployments_by_environment(
    db: Session,
    environment_id: int,
    limit: int = 10
) -> List[Deployment]:
    """Get recent deployments for an environment"""
    return db.query(Deployment).filter(
        Deployment.environment_id == environment_id
    ).order_by(Deployment.created_at.desc()).limit(limit).all()


def get_latest_deployment(
    db: Session,
    environment_id: int
) -> Optional[Deployment]:
    """Get the most recent deployment for an environment"""
    return db.query(Deployment).filter(
        Deployment.environment_id == environment_id
    ).order_by(Deployment.created_at.desc()).first()


def create_deployment(
    db: Session,
    environment: Environment,
    commit_sha: str,
    commit_message: Optional[str] = None
) -> Deployment:
    """Create a new deployment"""
    deployment = Deployment(
        environment_id=environment.id,
        commit_sha=commit_sha,
        commit_message=commit_message,
        status=DeploymentStatus.QUEUED
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    return deployment


def update_deployment_status(
    db: Session,
    deployment: Deployment,
    status: DeploymentStatus,
    error_message: Optional[str] = None,
    logs: Optional[str] = None
) -> Deployment:
    """Update deployment status"""
    deployment.status = status

    if status == DeploymentStatus.IN_PROGRESS and not deployment.started_at:
        deployment.started_at = datetime.utcnow()

    if status in [DeploymentStatus.SUCCESS, DeploymentStatus.FAILED]:
        deployment.completed_at = datetime.utcnow()

    if error_message:
        deployment.error_message = error_message

    if logs:
        deployment.logs = logs

    db.commit()
    db.refresh(deployment)
    return deployment
