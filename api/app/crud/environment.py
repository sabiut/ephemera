from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
from app.models.environment import Environment, EnvironmentStatus
from app.models.user import User


def get_environment_by_id(db: Session, environment_id: int) -> Optional[Environment]:
    """Get environment by ID"""
    return db.query(Environment).filter(Environment.id == environment_id).first()


def get_environment(db: Session, environment_id: int) -> Optional[Environment]:
    """Alias for get_environment_by_id for backward compatibility"""
    return get_environment_by_id(db, environment_id)


def get_environment_by_pr(
    db: Session,
    repository_full_name: str,
    pr_number: int
) -> Optional[Environment]:
    """Get environment by repository and PR number"""
    return db.query(Environment).filter(
        Environment.repository_full_name == repository_full_name,
        Environment.pr_number == pr_number
    ).first()


def get_environment_by_namespace(db: Session, namespace: str) -> Optional[Environment]:
    """Get environment by Kubernetes namespace"""
    return db.query(Environment).filter(Environment.namespace == namespace).first()


def get_environments_by_repo(db: Session, repository_full_name: str) -> List[Environment]:
    """Get all environments for a repository"""
    return db.query(Environment).filter(
        Environment.repository_full_name == repository_full_name
    ).all()


def get_active_environments(db: Session) -> List[Environment]:
    """Get all active environments"""
    return db.query(Environment).filter(
        Environment.status.in_([
            EnvironmentStatus.PENDING,
            EnvironmentStatus.PROVISIONING,
            EnvironmentStatus.READY,
            EnvironmentStatus.UPDATING
        ])
    ).all()


def create_environment(
    db: Session,
    repository_full_name: str,
    repository_name: str,
    pr_number: int,
    pr_title: str,
    branch_name: str,
    commit_sha: str,
    installation_id: int,
    owner: User,
    environment_url: Optional[str] = None
) -> Environment:
    """Create a new environment"""
    env = Environment(
        repository_full_name=repository_full_name,
        repository_name=repository_name,
        pr_number=pr_number,
        pr_title=pr_title,
        branch_name=branch_name,
        commit_sha=commit_sha,
        installation_id=installation_id,
        owner_id=owner.id,
        environment_url=environment_url,
        status=EnvironmentStatus.PENDING
    )

    # Generate namespace
    env.namespace = env.generate_namespace()

    db.add(env)
    db.commit()
    db.refresh(env)
    return env


def update_environment_status(
    db: Session,
    environment: Environment,
    status: EnvironmentStatus,
    error_message: Optional[str] = None
) -> Environment:
    """Update environment status"""
    environment.status = status
    if error_message:
        environment.error_message = error_message
    if status == EnvironmentStatus.READY:
        environment.last_deployed_at = datetime.utcnow()
    if status == EnvironmentStatus.DESTROYED:
        environment.destroyed_at = datetime.utcnow()

    db.commit()
    db.refresh(environment)
    return environment


def update_environment_commit(
    db: Session,
    environment: Environment,
    commit_sha: str
) -> Environment:
    """Update environment with new commit"""
    environment.commit_sha = commit_sha
    environment.status = EnvironmentStatus.UPDATING
    db.commit()
    db.refresh(environment)
    return environment


def delete_environment(db: Session, environment: Environment) -> None:
    """Delete environment from database"""
    db.delete(environment)
    db.commit()
