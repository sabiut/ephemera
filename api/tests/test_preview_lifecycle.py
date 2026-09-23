"""
A preview is torn down whenever its PR closes, is only called Destroyed once
its namespace is gone, and is never retried after its PR has closed.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.api.webhooks as webhooks
import app.tasks.cleanup as cleanup
import app.tasks.environment as env_tasks
from app.crud import deployment as deployment_crud
from app.crud import environment as environment_crud
from app.models import DeploymentStatus
from app.services.provisioning import EnvironmentRequest, request_environment
from app.models import Environment, EnvironmentStatus
from app.services.kubernetes import KubernetesService as K
from tests.test_readiness import _payload, _Session


@pytest.fixture()
def db(db_session, monkeypatch):
    for module in (cleanup, env_tasks):
        monkeypatch.setattr(module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(webhooks, "SessionLocal", _Session(db_session))
    monkeypatch.setattr(db_session, "close", lambda: None)
    return db_session


def _env(db, user, status, error=None, pr=3):
    env = Environment(
        repository_full_name="acme/app", repository_name="app", pr_number=pr, pr_title="t", branch_name="b",
        commit_sha="c" * 40, installation_id=1, owner_id=user.id, status=status, error_message=error,
    )
    env.namespace = env.generate_namespace()
    db.add(env)
    db.commit()
    deployment_crud.create_deployment(db, env, env.commit_sha)
    env.updated_at = datetime.now(timezone.utc)
    db.commit()
    return env


def _run(task, **kwargs):
    task._db = None
    try:
        return task.run(**kwargs)
    finally:
        task._db = None


@pytest.fixture()
def k8s(monkeypatch):
    state = {"outcome": K.DELETE_STARTED, "gone": True, "calls": []}

    def delete(ns):
        state["calls"].append(ns)
        return state["outcome"]

    monkeypatch.setattr(env_tasks.kubernetes_service, "delete_namespace", delete)
    monkeypatch.setattr(env_tasks.kubernetes_service, "wait_for_namespace_gone", lambda ns, timeout_seconds: state["gone"])
    monkeypatch.setattr(env_tasks.github_service, "post_comment_to_pr", lambda *a, **k: True)
    return state


# ------------------------------------------------------------------ destroy task

@pytest.mark.parametrize("outcome,gone,final", [
    (K.DELETE_ABSENT, None, EnvironmentStatus.DESTROYED),
    (K.DELETE_STARTED, True, EnvironmentStatus.DESTROYED),
    (K.DELETE_STARTED, False, EnvironmentStatus.DESTROYING),  # still terminating: not "Destroyed" yet
    (K.DELETE_ERROR, None, EnvironmentStatus.DESTROYING),     # API failure: the hourly job retries
    (K.DELETE_REFUSED, None, EnvironmentStatus.FAILED),
])
def test_destroyed_only_when_the_namespace_is_gone(db, user, k8s, outcome, gone, final):
    env = _env(db, user, EnvironmentStatus.READY)
    environment_crud.mark_closed(db, env)
    k8s["outcome"], k8s["gone"] = outcome, gone
    _run(env_tasks.destroy_environment, environment_id=env.id)
    db.refresh(env)
    assert env.status == final


def test_stale_destroying_is_confirmed_before_marking_destroyed(db, user, monkeypatch):
    env = _env(db, user, EnvironmentStatus.DESTROYING)
    env.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db.commit()
    outcomes = iter([K.DELETE_STARTED, K.DELETE_ABSENT])
    monkeypatch.setattr(cleanup.kubernetes_service, "delete_namespace", lambda ns: next(outcomes))
    monkeypatch.setattr(cleanup.kubernetes_service, "namespace_exists", lambda ns: True)

    _run(cleanup.cleanup_stale_environments)
    db.refresh(env)
    assert env.status == EnvironmentStatus.DESTROYING  # deletion requested, not yet gone

    env.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db.commit()
    _run(cleanup.cleanup_stale_environments)
    db.refresh(env)
    assert env.status == EnvironmentStatus.DESTROYED


# ------------------------------------------------------------------ close handler

@pytest.fixture()
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(webhooks.destroy_environment, "delay", lambda **kw: calls.append(kw))
    monkeypatch.setattr(webhooks.github_service, "post_comment_to_pr", lambda *a, **k: True)
    return calls


@pytest.mark.parametrize("status", [
    EnvironmentStatus.FAILED,      # the review's reproduction: zero cleanup tasks were queued
    EnvironmentStatus.DESTROYING,
    EnvironmentStatus.READY,
    EnvironmentStatus.PENDING,
])
def test_closing_a_pr_tears_down_any_live_or_failed_preview(db, user, queued, status):
    env = _env(db, user, status)
    webhooks.handle_pull_request_closed(_payload("closed"))
    assert queued and queued[0]["environment_id"] == env.id


def test_closing_a_pr_whose_preview_is_destroyed_queues_nothing_but_is_recorded(db, user, queued):
    env = _env(db, user, EnvironmentStatus.DESTROYED)
    webhooks.handle_pull_request_closed(_payload("closed"))
    assert queued == []
    db.refresh(env)
    assert env.closed_at is not None  # a deploy still queued must see it


# ------------------------------------------------------------------ ordering after close / reopen

def test_a_deploy_queued_before_the_close_does_not_recreate_the_preview(db, user, monkeypatch):
    # The review's reproduction: cleanup finished first, then a provision for
    # the same commit took the lock and ran against a DESTROYED environment.
    env = _env(db, user, EnvironmentStatus.DESTROYED)
    environment_crud.mark_closed(db, env)
    record = deployment_crud.create_deployment(db, env, env.commit_sha)
    created = []
    monkeypatch.setattr(env_tasks.kubernetes_service, "create_namespace", lambda *a, **k: created.append(a))
    result = _run(env_tasks.provision_environment, environment_id=env.id,
                  commit_sha=env.commit_sha, deployment_id=record.id)
    assert result["skipped"] == "pull request closed"
    assert created == []
    db.refresh(env), db.refresh(record)
    assert env.status == EnvironmentStatus.DESTROYED
    assert record.status == DeploymentStatus.FAILED


def test_an_update_queued_before_the_close_stands_down(db, user):
    env = _env(db, user, EnvironmentStatus.READY)
    environment_crud.mark_closed(db, env)
    result = _run(env_tasks.update_environment, environment_id=env.id, commit_sha=env.commit_sha)
    assert result["skipped"] == "pull request closed"


def _reopen(db, user):
    return request_environment(db, EnvironmentRequest(
        repository_full_name="acme/app", repository_name="app", pr_number=3, pr_title="t",
        branch_name="b", commit_sha="c" * 40, installation_id=1, owner=user))


def test_reopening_authorizes_provisioning_again(db, user, retry_calls):
    env = _env(db, user, EnvironmentStatus.DESTROYED)
    environment_crud.mark_closed(db, env)
    _, action = _reopen(db, user)
    assert action == "reprovisioned" and len(retry_calls["provision"]) == 1
    db.refresh(env)
    assert env.closed_at is None
    ran = []
    env_tasks._locked(env_tasks.provision_environment, env.id, env.commit_sha, None, lambda: ran.append(1))
    assert ran == [1]


def test_a_teardown_queued_before_a_reopen_leaves_the_preview_alone(db, user, k8s, retry_calls):
    # Closed and reopened before the teardown ran: the preview is still up.
    env = _env(db, user, EnvironmentStatus.READY)
    environment_crud.mark_closed(db, env)
    _, action = _reopen(db, user)
    assert action == "exists"
    result = _run(env_tasks.destroy_environment, environment_id=env.id)
    assert result["skipped"] == "pull request reopened"
    assert k8s["calls"] == []
    db.refresh(env)
    assert env.status == EnvironmentStatus.READY


# ------------------------------------------------------------------ retry task

@pytest.fixture()
def retry_calls(monkeypatch):
    calls = {"provision": [], "destroy": []}
    monkeypatch.setattr(env_tasks.provision_environment, "delay", lambda **kw: calls["provision"].append(kw))
    monkeypatch.setattr(env_tasks.destroy_environment, "delay", lambda **kw: calls["destroy"].append(kw))
    return calls


def _pr_state(monkeypatch, state):
    import app.services.github as gh

    def get_pull_request(installation_id, repo, number):
        if state == "error":
            raise RuntimeError("GitHub unavailable")
        return None if state == "missing" else SimpleNamespace(state=state)

    monkeypatch.setattr(gh.github_service, "get_pull_request", get_pull_request)


@pytest.mark.parametrize("state,provisioned,destroyed", [
    ("open", 1, 0),
    ("closed", 0, 1),   # a closed PR is cleaned up, never retried
    ("missing", 0, 1),
    ("error", 0, 0),    # GitHub unreachable: no blind retry
])
def test_retry_respects_the_pull_requests_state(db, user, retry_calls, monkeypatch, state, provisioned, destroyed):
    env = _env(db, user, EnvironmentStatus.FAILED, error="Preview URLs did not answer: web (HTTP 503)")
    _pr_state(monkeypatch, state)
    _run(cleanup.retry_failed_environments, max_age_hours=1)
    assert len(retry_calls["provision"]) == provisioned
    assert len(retry_calls["destroy"]) == destroyed
    db.refresh(env)
    assert (env.closed_at is not None) == bool(destroyed)  # else the teardown would stand down
