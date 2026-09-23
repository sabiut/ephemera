from sqlalchemy import or_
from sqlalchemy.orm import Query, Session
from typing import List, Optional, Set
from datetime import datetime, timezone
from app.models.environment import Environment, EnvironmentStatus
from app.models.user import User

ACTIVE_STATUSES = (
    EnvironmentStatus.PENDING,
    EnvironmentStatus.PROVISIONING,
    EnvironmentStatus.READY,
    EnvironmentStatus.UPDATING,
)


def visible_environments(db: Session, user: User, admin: bool, repo_names: Optional[Set[str]] = None) -> Query:
    """
    Environments this user may see.

    Every environment is owned by the PR author, so a user always sees their
    own. They also see every environment in repositories where they are a
    collaborator (``repo_names``), which is what lets reviewers and QA open
    teammates' previews. Admins see everything.
    """
    query = db.query(Environment)
    if admin:
        return query
    condition = Environment.owner_id == user.id
    if repo_names:
        condition = or_(condition, Environment.repository_full_name.in_(sorted(repo_names)))
    return query.filter(condition)


def list_environments(
    db: Session,
    user: User,
    admin: bool,
    repository: Optional[str] = None,
    active_only: bool = False,
    limit: int = 100,
    repo_names: Optional[Set[str]] = None,
) -> List[Environment]:
    """List visible environments, newest first, with optional filters."""
    query = visible_environments(db, user, admin, repo_names)
    if repository:
        query = query.filter(Environment.repository_full_name == repository)
    if active_only:
        query = query.filter(Environment.status.in_(ACTIVE_STATUSES))
    return query.order_by(Environment.created_at.desc(), Environment.id.desc()).limit(limit).all()


def get_visible_environment(
    db: Session, user: User, admin: bool, environment_id: Optional[int] = None,
    namespace: Optional[str] = None, repo_names: Optional[Set[str]] = None,
) -> Optional[Environment]:
    """Fetch one environment by id or namespace, or None if absent or not visible."""
    query = visible_environments(db, user, admin, repo_names)
    if environment_id is not None:
        query = query.filter(Environment.id == environment_id)
    if namespace is not None:
        query = query.filter(Environment.namespace == namespace)
    return query.first()


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
    return db.query(Environment).filter(Environment.status.in_(ACTIVE_STATUSES)).all()


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
    _queue(env)

    # Generate namespace
    env.namespace = env.generate_namespace()

    db.add(env)
    db.commit()
    db.refresh(env)
    return env


# Statuses that end or replace a deployment's progress.
_STATUS_STAGES = {
    EnvironmentStatus.READY: "ready",
    EnvironmentStatus.FAILED: "failed",
    EnvironmentStatus.DESTROYING: "destroying",
    EnvironmentStatus.DESTROYED: "destroyed",
}


def _stage(environment: Environment, stage: str, detail: Optional[str] = None) -> None:
    environment.stage = stage
    environment.stage_detail = detail
    environment.stage_started_at = datetime.now(timezone.utc)


def _queue(environment: Environment) -> None:
    """A new deployment was requested: progress starts again at "queued"."""
    _stage(environment, "queued")
    environment.deploy_started_at = environment.stage_started_at


def set_stage(db: Session, environment_id: int, stage: str, detail: Optional[str] = None,
              commit_sha: Optional[str] = None) -> None:
    """
    Record the deployment's progress. A task whose commit is no longer the
    PR's latest writes nothing, so an overtaken deploy cannot show its
    stages against the newer commit.
    """
    environment = get_environment(db, environment_id)
    if environment is None:
        return
    db.refresh(environment)
    if commit_sha and environment.commit_sha != commit_sha:
        return
    _stage(environment, stage, detail)
    db.commit()


def update_environment_status(
    db: Session,
    environment: Environment,
    status: EnvironmentStatus,
    error_message: Optional[str] = None
) -> Environment:
    """Update environment status"""
    environment.status = status
    if status in _STATUS_STAGES:
        _stage(environment, _STATUS_STAGES[status])
    if error_message:
        environment.error_message = error_message
    if status == EnvironmentStatus.READY:
        environment.last_deployed_at = datetime.now(timezone.utc)
    if status == EnvironmentStatus.DESTROYED:
        environment.destroyed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(environment)
    return environment


def reset_environment(
    db: Session,
    environment: Environment,
    pr_title: Optional[str],
    branch_name: str,
    commit_sha: str,
    installation_id: int,
    environment_url: Optional[str] = None,
) -> Environment:
    """Bring a destroyed/failed environment back to PENDING for re-provisioning."""
    environment.pr_title = pr_title
    environment.branch_name = branch_name
    environment.commit_sha = commit_sha
    environment.installation_id = installation_id
    if environment_url:
        environment.environment_url = environment_url
    environment.status = EnvironmentStatus.PENDING
    environment.error_message = None
    environment.destroyed_at = None
    _queue(environment)
    environment.closed_at = None  # a reopen authorizes provisioning again
    db.commit()
    db.refresh(environment)
    return environment


def mark_closed(db: Session, environment: Environment) -> Environment:
    """Record that the PR closed, so queued deploys stand down."""
    if environment.closed_at is None:
        environment.closed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(environment)
    return environment


def mark_reopened(db: Session, environment: Environment) -> Environment:
    """Clear the closed marker, so a queued teardown stands down."""
    environment.closed_at = None
    db.commit()
    db.refresh(environment)
    return environment


def record_service_urls(
    db: Session,
    environment: Environment,
    service_urls: dict,
    primary_url: Optional[str],
) -> Environment:
    """Store the real per-service URLs and the one a reviewer should open."""
    environment.service_urls = dict(service_urls)
    if primary_url:
        environment.environment_url = primary_url
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
    _queue(environment)
    db.commit()
    db.refresh(environment)
    return environment


def delete_environment(db: Session, environment: Environment) -> None:
    """Delete environment from database"""
    db.delete(environment)
    db.commit()
