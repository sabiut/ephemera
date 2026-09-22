"""
Scheduled cleanup, safe automatic retries, and an AI cache key that covers
every file the model sees.
"""

from datetime import datetime, timedelta, timezone

import pytest

import app.tasks.cleanup as cleanup
from app.core.celery_app import celery_app
from app.crud import deployment as deployment_crud
from app.models import Deployment, Environment, EnvironmentStatus
from app.services.ai_deployment import AIDeploymentService, RepoContext


# ------------------------------------------------------------------ AI cache key

def _service():
    return AIDeploymentService(None, None, None, provider=None, enabled=False)


def _ctx(compose="services: {}", **files):
    return RepoContext(compose_content=compose, compose_filename="docker-compose.yml", additional_files=dict(files))


def test_cache_key_changes_when_a_dockerfile_changes():
    svc = _service()
    before = svc._get_cache_key(_ctx(Dockerfile="FROM node:20"), "pr-1-app")
    after = svc._get_cache_key(_ctx(Dockerfile="FROM node:22"), "pr-1-app")
    assert before != after


def test_cache_key_is_stable_and_order_independent():
    svc = _service()
    a = svc._get_cache_key(_ctx(**{"Dockerfile": "x", "package.json": "{}"}), "pr-1-app")
    ctx = _ctx()
    ctx.additional_files["package.json"] = "{}"
    ctx.additional_files["Dockerfile"] = "x"
    assert a == svc._get_cache_key(ctx, "pr-1-app")


def test_cache_key_separates_namespaces_and_file_boundaries():
    svc = _service()
    assert svc._get_cache_key(_ctx(), "pr-1-app") != svc._get_cache_key(_ctx(), "pr-2-app")
    # moving text between files must not collide
    assert svc._get_cache_key(_ctx(a="xy", b=""), "n") != svc._get_cache_key(_ctx(a="x", b="y"), "n")


# ------------------------------------------------------------------ transient classification

@pytest.mark.parametrize("message,transient", [
    ("Preview URLs did not answer: web (HTTP 503)", True),
    ("Services did not become ready: web (pod is still Pending (image pull or scheduling))", True),
    ("Services did not become ready: web (pod is Running but its readiness probe has not passed)", True),
    ("Failed to create Kubernetes namespace", True),
    ("No docker-compose.yml in the repository, so there is nothing to preview", False),
    ("Nothing was deployed: every service is build-only (web)", False),
    ("Services did not become ready: web (CrashLoopBackOff: container keeps crashing)", False),
    ("Services did not become ready: web (image r/web:abc was never published; check CI)", False),
    ("Services did not become ready: web (InvalidImageName: bad)", False),
    (None, False),
])
def test_transient_failures_are_told_apart(message, transient):
    assert cleanup.is_transient_failure(message) is transient


# ------------------------------------------------------------------ retry task

@pytest.fixture()
def task_db(db_session, monkeypatch):
    """Run tasks against the test session."""
    monkeypatch.setattr(cleanup, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    yield db_session


@pytest.fixture()
def queued(monkeypatch):
    calls = []
    import app.tasks.environment as env_tasks
    monkeypatch.setattr(env_tasks.provision_environment, "delay", lambda **kw: calls.append(kw))
    return calls


def _failed_env(db, user, pr, error, attempts=1):
    env = Environment(
        repository_full_name="acme/app", repository_name="app", pr_number=pr, pr_title="t", branch_name="b",
        commit_sha="c" * 40, installation_id=1, owner_id=user.id, status=EnvironmentStatus.FAILED,
        error_message=error,
    )
    env.namespace = env.generate_namespace()
    db.add(env)
    db.commit()
    for _ in range(attempts):
        deployment_crud.create_deployment(db, env, env.commit_sha)
    env.updated_at = datetime.now(timezone.utc)
    db.commit()
    return env


def _run_retry():
    task = cleanup.retry_failed_environments
    task._db = None
    try:
        return task.run(max_age_hours=1)
    finally:
        task._db = None


def test_transient_failure_is_retried_exactly_once(task_db, queued, user):
    env = _failed_env(task_db, user, 1, "Preview URLs did not answer: web (HTTP 503)")
    first = _run_retry()
    assert first["retried"] == [env.id] and queued == [{"environment_id": env.id}]
    assert task_db.query(Deployment).filter_by(environment_id=env.id).count() == 2

    second = _run_retry()  # still FAILED, but this commit already had its retry
    assert second["retried"] == [] and len(queued) == 1


def test_deterministic_failures_are_never_retried(task_db, queued, user):
    _failed_env(task_db, user, 2, "Nothing was deployed: every service is build-only (web)")
    _failed_env(task_db, user, 3, "Services did not become ready: web (CrashLoopBackOff: container keeps crashing)")
    result = _run_retry()
    assert result["retried"] == [] and result["skipped"] == 2 and queued == []


def test_old_failures_are_left_alone(task_db, queued, user):
    env = _failed_env(task_db, user, 4, "Preview URLs did not answer: web (HTTP 503)")
    env.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)
    task_db.commit()
    assert _run_retry()["retried"] == []


# ------------------------------------------------------------------ schedules

def test_both_cleanup_jobs_are_scheduled():
    schedule = {entry["task"]: entry for entry in celery_app.conf.beat_schedule.values()}
    assert schedule["app.tasks.cleanup.retry_failed_environments"]["schedule"] == 900.0
    assert schedule["app.tasks.cleanup.cleanup_old_environments"]["kwargs"] == {"days": 7}
    assert "app.tasks.cleanup.cleanup_stale_environments" in schedule
