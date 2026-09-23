"""
The dashboard shows where a deployment is: queued, deploying, waiting for
the commit's image, starting services, checking HTTPS, then ready or failed,
with when each began.
"""

from types import SimpleNamespace

import pytest

import app.tasks.environment as env_tasks
from app.crud import deployment as deployment_crud
from app.crud import environment as environment_crud
from app.models import EnvironmentStatus
from tests.test_readiness import _k8s, _pod, environment, wired  # noqa: F401 (fixtures)

SHA = "a" * 40


@pytest.fixture()
def db(db_session, monkeypatch):
    monkeypatch.setattr(env_tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    return db_session


@pytest.fixture()
def stages(monkeypatch):
    seen = []
    real = environment_crud.set_stage

    def record(db, environment_id, stage, detail=None, commit_sha=None):
        seen.append(stage)
        return real(db, environment_id, stage, detail, commit_sha=commit_sha)

    monkeypatch.setattr(environment_crud, "set_stage", record)
    return seen


def test_a_new_request_starts_at_queued(db, environment):
    environment_crud.update_environment_commit(db, environment, SHA)
    assert environment.stage == "queued"
    assert environment.deploy_started_at == environment.stage_started_at is not None


def test_a_deploy_walks_through_its_stages_and_ends_ready(db, environment, wired, stages, monkeypatch):
    monkeypatch.setattr(env_tasks.kubernetes_service, "namespace_exists", lambda ns: True)
    monkeypatch.setattr(env_tasks, "_notify", lambda *a, **k: None)
    environment_crud.update_environment_commit(db, environment, SHA)
    rec = deployment_crud.create_deployment(db, environment, SHA)
    env_tasks.update_environment._db = None
    env_tasks.update_environment.run(environment_id=environment.id, commit_sha=SHA, deployment_id=rec.id)
    assert stages == ["deploying", "starting", "checking_https"]
    db.refresh(environment)
    assert environment.status == EnvironmentStatus.READY and environment.stage == "ready"


def test_a_failure_ends_the_progress_as_failed(db, environment):
    environment_crud.update_environment_status(db, environment, EnvironmentStatus.FAILED, error_message="x")
    assert environment.stage == "failed"


def test_an_overtaken_task_does_not_write_progress_for_the_newer_commit(db, environment):
    environment_crud.update_environment_commit(db, environment, "b" * 40)  # newer push
    environment_crud.set_stage(db, environment.id, "checking_https", commit_sha=SHA)  # older task
    db.refresh(environment)
    assert environment.stage == "queued"


def test_progress_is_in_the_api(client, auth_headers, db_session, environment):
    environment_crud.update_environment_commit(db_session, environment, SHA)
    environment_crud.set_stage(db_session, environment.id, "waiting_for_image", "web image built from aaaaaaa", commit_sha=SHA)
    body = client.get("/api/v1/environments/", headers=auth_headers).json()[0]
    assert body["stage"] == "waiting_for_image" and body["stage_detail"].startswith("web image")
    assert body["stage_started_at"] and body["deploy_started_at"]


def test_waiting_for_the_image_then_starting_are_both_reported():
    state = {"polls": 0}
    running = SimpleNamespace(waiting=None, terminated=None, running=SimpleNamespace(started_at="now"))

    def pods():
        state["polls"] += 1
        if state["polls"] < 3:
            return [_pod("ImagePullBackOff", "not found", phase="Pending", image=f"r/web:{SHA}")]
        pod = _pod(image=f"r/web:{SHA}")
        pod.status.container_statuses[0].state = running
        return [pod]

    k8s = _k8s({"web": {"ready": 0}}, pods=pods)
    k8s.IMAGE_RETRY_SECONDS = 999
    orig = k8s.apps_v1.read_namespaced_deployment

    def read(name, namespace):
        dep = orig(name, namespace)
        if state["polls"] >= 6:
            dep.status.ready_replicas = dep.status.updated_replicas = 1
        return dep

    k8s.apps_v1.read_namespaced_deployment = read
    events = []
    ready, problems = k8s.wait_for_deployments_ready(
        "ns", ["web"], timeout_seconds=0, poll_seconds=0, image_wait_seconds=5,
        commit_markers=(SHA, SHA[:7]),
        on_waiting_for_image=lambda s, i: events.append("waiting"),
        on_image_available=lambda: events.append("available"),
    )
    assert ready == ["web"]
    assert events == ["waiting", "available"]  # each once, in order
