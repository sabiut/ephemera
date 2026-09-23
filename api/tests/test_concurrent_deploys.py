"""
Commits arriving close together: each task writes only its own deployment
record, only one task changes a preview at a time, and an overtaken task
never deploys or reports.
"""

from contextlib import contextmanager

import pytest
import redis

import app.core.locks as locks
import app.tasks.environment as env_tasks
from app.crud import deployment as deployment_crud
from app.crud import environment as environment_crud
from app.models import DeploymentStatus, EnvironmentStatus
from tests.test_readiness import environment, wired  # noqa: F401 (fixtures)

A, B = "a" * 40, "b" * 40


@pytest.fixture()
def db(db_session, monkeypatch):
    monkeypatch.setattr(env_tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    return db_session


@pytest.fixture()
def lock_log(monkeypatch):
    log = {"held": [], "busy": False}

    @contextmanager
    def fake_lock(environment_id):
        log["held"].append(environment_id)
        yield not log["busy"]

    monkeypatch.setattr(env_tasks, "environment_lock", fake_lock)
    return log


@pytest.fixture()
def quiet(monkeypatch):
    notes = []
    monkeypatch.setattr(env_tasks, "_notify", lambda *a, **k: notes.append(a))
    monkeypatch.setattr(env_tasks.kubernetes_service, "namespace_exists", lambda ns: True)
    return notes


def _run(task, **kwargs):
    task._db = None
    try:
        return task.run(**kwargs)
    finally:
        task._db = None


def test_each_task_updates_only_its_own_record(db, environment, wired):
    rec_a = deployment_crud.create_deployment(db, environment, A)
    rec_b = deployment_crud.create_deployment(db, environment, B)  # newer, so "latest"
    environment.commit_sha = A
    db.commit()
    env_tasks._run_deployment(db, environment.id, 1, "acme/app", environment.namespace, A, deployment_id=rec_a.id)
    db.refresh(rec_a), db.refresh(rec_b)
    assert rec_a.status == DeploymentStatus.SUCCESS
    assert rec_b.status == DeploymentStatus.QUEUED  # before: A's result landed here


def test_a_task_overtaken_before_it_starts_never_deploys(db, environment, wired, lock_log, quiet):
    rec_a = deployment_crud.create_deployment(db, environment, A)
    environment_crud.update_environment_commit(db, environment, B)  # B was pushed while A waited
    result = _run(env_tasks.update_environment, environment_id=environment.id, commit_sha=A, deployment_id=rec_a.id)
    assert result["superseded_by"] == B
    assert wired["waited_for"] is None  # nothing deployed
    db.refresh(rec_a)
    assert rec_a.status == DeploymentStatus.FAILED and "Superseded by newer commit bbbbbbb" in rec_a.error_message
    assert quiet == []  # no status or comment for a stale commit
    assert lock_log["held"] == [environment.id]


def test_a_push_during_a_deploy_leaves_the_preview_to_the_newer_task(db, environment, wired, lock_log, quiet, monkeypatch):
    environment_crud.update_environment_commit(db, environment, A)
    rec_a = deployment_crud.create_deployment(db, environment, A)
    real_wait = env_tasks.kubernetes_service.wait_for_deployments_ready

    def push_arrives_mid_deploy(*args, **kwargs):
        environment_crud.update_environment_commit(db, environment, B)
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(env_tasks.kubernetes_service, "wait_for_deployments_ready", push_arrives_mid_deploy)
    result = _run(env_tasks.update_environment, environment_id=environment.id, commit_sha=A, deployment_id=rec_a.id)
    assert result["superseded_by"] == B
    db.refresh(environment), db.refresh(rec_a)
    assert environment.status == EnvironmentStatus.UPDATING  # not overwritten to READY by the stale task
    assert rec_a.status == DeploymentStatus.FAILED
    assert quiet == []


def test_current_commit_deploys_normally_under_the_lock(db, environment, wired, lock_log, quiet):
    environment_crud.update_environment_commit(db, environment, A)
    rec = deployment_crud.create_deployment(db, environment, A)
    result = _run(env_tasks.update_environment, environment_id=environment.id, commit_sha=A, deployment_id=rec.id)
    assert result["success"] is True
    db.refresh(environment)
    assert environment.status == EnvironmentStatus.READY
    assert lock_log["held"] == [environment.id]


def test_a_busy_lock_runs_nothing(db, environment, wired, lock_log, quiet):
    lock_log["busy"] = True
    result = _run(env_tasks.update_environment, environment_id=environment.id, commit_sha=environment.commit_sha)
    assert result["error"] == "environment busy"
    assert wired["waited_for"] is None


# ------------------------------------------------------------------ the lock itself

class FakeRedis:
    def __init__(self, acquire=True, fail=False):
        self.acquire_result, self.fail, self.released, self.names = acquire, fail, [], []

    def lock(self, name, timeout, blocking_timeout):
        if self.fail:
            raise redis.ConnectionError("down")
        self.names.append((name, timeout, blocking_timeout))
        outer = self

        class L:
            def acquire(self):
                return outer.acquire_result

            def release(self):
                outer.released.append(name)

        return L()


@pytest.mark.parametrize("fake,expected,released", [
    (FakeRedis(), True, 1),
    (FakeRedis(acquire=False), False, 0),
    (FakeRedis(fail=True), True, 0),  # Redis down: fail open
])
def test_environment_lock(monkeypatch, fake, expected, released):
    monkeypatch.setattr(locks, "_redis", lambda: fake)
    with locks.environment_lock(42) as held:
        assert held is expected
    assert len(fake.released) == released
    if fake.names:
        assert fake.names[0][0] == "ephemera:environment-lock:42"
