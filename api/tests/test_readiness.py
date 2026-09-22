"""
"Ready" must mean a reviewer can open the preview.

Covers the readiness wait, the public URL probe, the honest failure paths
in _run_deployment, persistence of the real service URLs, and the webhook
rule that a push after a failure starts provisioning again.
"""

from types import SimpleNamespace

import pytest

import app.api.webhooks as webhooks
import app.tasks.environment as tasks
from app.crud import deployment as deployment_crud
from app.crud import environment as environment_crud
from app.models import DeploymentStatus, Environment, EnvironmentStatus
from app.schemas.github import PullRequestWebhook
from app.services.deployment import choose_primary_url, probe_urls
from app.services.kubernetes import KubernetesService


# ----------------------------------------------------------------- primary URL

def test_primary_url_prefers_frontend_names_then_compose_order():
    urls = {"api": "https://a", "web": "https://w", "worker-ui": "https://u"}
    assert choose_primary_url(["api", "web", "worker-ui"], urls) == "https://w"
    assert choose_primary_url(["db", "echo"], {"echo": "https://e"}) == "https://e"
    assert choose_primary_url(["db"], {}) is None


# ----------------------------------------------------------------- URL probe

def test_probe_accepts_any_non_5xx_and_reports_the_rest(monkeypatch):
    import httpx

    def fake_get(url, **kwargs):
        if "web" in url:
            return SimpleNamespace(status_code=404)  # app has no "/" route; still reachable
        if "api" in url:
            return SimpleNamespace(status_code=503)
        raise httpx.ConnectError("TLS handshake failed")

    monkeypatch.setattr(httpx, "get", fake_get)
    unreachable = probe_urls(
        {"web": "https://web", "api": "https://api", "echo": "https://echo"},
        timeout_seconds=0.01, poll_seconds=0,
    )
    assert set(unreachable) == {"api", "echo"}
    assert unreachable["api"] == "HTTP 503"
    assert unreachable["echo"].startswith("ConnectError")


# ----------------------------------------------------------------- readiness wait

def _k8s(deployments, pods=None, selector=None):
    """Fake KubernetesService. pods may be a list or a callable returning one."""
    svc = KubernetesService.__new__(KubernetesService)
    svc.enabled = True
    svc.deleted = []
    svc.selectors = []

    def read(name, namespace):
        dep = deployments[name]
        return SimpleNamespace(
            spec=SimpleNamespace(
                replicas=dep.get("replicas", 1),
                selector=SimpleNamespace(match_labels=selector or {"app": "app", "service": name}),
            ),
            status=SimpleNamespace(
                ready_replicas=dep.get("ready", 0),
                updated_replicas=dep.get("updated", dep.get("ready", 0)),
                conditions=[],
            ),
        )

    def list_pods(namespace, label_selector):
        svc.selectors.append(label_selector)
        items = pods() if callable(pods) else (pods or [])
        return SimpleNamespace(items=items)

    svc.apps_v1 = SimpleNamespace(read_namespaced_deployment=read)
    svc.core_v1 = SimpleNamespace(
        list_namespaced_pod=list_pods,
        delete_namespaced_pod=lambda name, namespace: svc.deleted.append(name),
    )
    return svc


def _pod(waiting_reason=None, waiting_message=None, phase="Running", image="nginx", restarts=0, name="pod-1"):
    state = SimpleNamespace(
        waiting=SimpleNamespace(reason=waiting_reason, message=waiting_message) if waiting_reason else None,
        terminated=None,
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(
            phase=phase,
            container_statuses=[SimpleNamespace(state=state, image=image, restart_count=restarts, last_state=None)],
            init_container_statuses=[],
            conditions=[],
        ),
    )


def test_wait_returns_ready_when_all_replicas_are_updated_and_ready():
    k8s = _k8s({"web": {"replicas": 1, "ready": 1}, "db": {"replicas": 1, "ready": 1}})
    ready, problems = k8s.wait_for_deployments_ready("ns", ["web", "db"], timeout_seconds=1, poll_seconds=0)
    assert sorted(ready) == ["db", "web"]
    assert problems == {}


def test_wait_explains_an_image_pull_failure():
    k8s = _k8s(
        {"web": {"replicas": 1, "ready": 0}},
        pods=[_pod("ImagePullBackOff", 'Back-off pulling image "ghcr.io/acme/web:abc"\nmore detail', phase="Pending")],
    )
    ready, problems = k8s.wait_for_deployments_ready("ns", ["web"], timeout_seconds=0.01, poll_seconds=0)
    assert ready == []
    assert problems == {"web": 'ImagePullBackOff: Back-off pulling image "ghcr.io/acme/web:abc"'}


def test_wait_explains_a_running_pod_that_never_passes_readiness():
    k8s = _k8s({"web": {"replicas": 1, "ready": 0}}, pods=[_pod(phase="Running")])
    _, problems = k8s.wait_for_deployments_ready("ns", ["web"], timeout_seconds=0.01, poll_seconds=0)
    assert "readiness probe" in problems["web"]


# ----------------------------------------------------------------- _run_deployment

@pytest.fixture()
def environment(db_session, user):
    env = environment_crud.create_environment(
        db=db_session, repository_full_name="acme/app", repository_name="app", pr_number=3,
        pr_title="t", branch_name="b", commit_sha="c" * 40, installation_id=1, owner=user,
        environment_url="https://pr-3-app.preview.test",
    )
    deployment_crud.create_deployment(db_session, env, "c" * 40)
    return env


def _deploy_result(**overrides):
    base = {
        "success": True, "compose_found": True,
        "services": ["web", "api"],
        "service_urls": {"web": "https://pr-3-app-web.preview.test", "api": "https://pr-3-app-api.preview.test"},
        "skipped_services": [], "applied_count": 6,
    }
    base.update(overrides)
    return base


@pytest.fixture()
def wired(monkeypatch):
    """Fake deploy service, readiness wait and probe; each test sets the outcomes."""
    state = {
        "deploy": _deploy_result(),
        "problems": {},
        "unreachable": {},
        "waited_for": None,
        "probed": None,
    }

    class FakeDeployService:
        def deploy_application(self, **kwargs):
            return dict(state["deploy"])

    def fake_wait(namespace, names, timeout_seconds, **kwargs):
        state["waited_for"] = list(names)
        state["wait_kwargs"] = kwargs
        return [n for n in names if n not in state["problems"]], dict(state["problems"])

    def fake_probe(urls, timeout_seconds):
        state["probed"] = dict(urls)
        return dict(state["unreachable"])

    monkeypatch.setattr(tasks, "_active_deployment_service", lambda: FakeDeployService())
    monkeypatch.setattr(tasks.kubernetes_service, "wait_for_deployments_ready", fake_wait)
    monkeypatch.setattr(tasks, "probe_urls", fake_probe)
    return state


def _run(db, env):
    return tasks._run_deployment(db, env.id, 1, "acme/app", env.namespace, "c" * 40)


def _latest(db, env):
    return deployment_crud.get_latest_deployment(db, env.id)


def test_success_requires_ready_pods_and_answering_urls_and_persists_them(db_session, environment, wired):
    result = _run(db_session, environment)
    assert result["success"] is True
    assert wired["waited_for"] == ["web", "api"]
    assert wired["probed"] == _deploy_result()["service_urls"]
    assert result["primary_url"] == "https://pr-3-app-web.preview.test"

    db_session.refresh(environment)
    assert environment.service_urls == _deploy_result()["service_urls"]
    assert environment.environment_url == "https://pr-3-app-web.preview.test"  # no longer the namespace URL
    assert _latest(db_session, environment).status == DeploymentStatus.SUCCESS


def test_missing_compose_is_a_failure_not_ready(db_session, environment, wired):
    # Mirrors what deploy_application returns for a repository with no compose file.
    wired["deploy"] = _deploy_result(
        success=False, compose_found=False, services=[], service_urls={},
        error="docker-compose.yml not found in repository",
    )
    result = _run(db_session, environment)
    assert result["success"] is False
    assert "docker-compose.yml" in result["error"]
    assert _latest(db_session, environment).status == DeploymentStatus.FAILED
    assert wired["waited_for"] is None


def test_all_services_skipped_is_a_failure_with_the_reason(db_session, environment, wired):
    wired["deploy"] = _deploy_result(services=[], service_urls={}, skipped_services=["web", "worker"], applied_count=0)
    result = _run(db_session, environment)
    assert result["success"] is False
    assert "build-only (web, worker)" in result["error"]
    assert "publish an image" in result["error"]


def test_pods_that_never_become_ready_fail_with_the_kubernetes_reason(db_session, environment, wired):
    wired["problems"] = {"api": "CrashLoopBackOff: exited with code 1"}
    result = _run(db_session, environment)
    assert result["success"] is False
    assert result["error"] == "Services did not become ready: api (CrashLoopBackOff: exited with code 1)"
    assert wired["probed"] is None  # no point probing URLs of pods that are not ready
    db_session.refresh(environment)
    assert environment.service_urls is None  # nothing persisted for a failed preview


def test_unanswering_urls_fail_after_pods_are_ready(db_session, environment, wired):
    wired["unreachable"] = {"web": "HTTP 503"}
    result = _run(db_session, environment)
    assert result["success"] is False
    assert result["error"] == "Preview URLs did not answer: web (HTTP 503)"


def test_summary_leads_with_the_primary_link():
    summary = tasks._deployment_summary(_deploy_result(primary_url="https://pr-3-app-web.preview.test"))
    assert summary.splitlines()[1].startswith("**Open preview**: https://pr-3-app-web.preview.test")


# ----------------------------------------------------------------- synchronize after failure

class _Session:
    """Context manager handing out the test session without closing it."""

    def __init__(self, db):
        self.db = db

    def __call__(self):
        return self

    def __enter__(self):
        return self.db

    def __exit__(self, *exc):
        return False


def _payload(action="synchronize", sha="d" * 40):
    return PullRequestWebhook(**{
        "action": action, "number": 3,
        "pull_request": {"id": 1, "number": 3, "title": "t", "state": "open",
                         "head": {"ref": "b", "sha": sha}, "base": {"ref": "main", "sha": "e" * 40},
                         "user": {"id": 1, "login": "octocat"}, "merged": False},
        "repository": {"id": 1, "name": "app", "full_name": "acme/app"},
        "sender": {"id": 1, "login": "octocat"},
        "installation": {"id": 1},
    })


@pytest.fixture()
def webhook_wiring(monkeypatch, db_session):
    calls = {"opened": 0, "update": 0, "status": 0}
    monkeypatch.setattr(webhooks, "SessionLocal", _Session(db_session))
    monkeypatch.setattr(webhooks, "handle_pull_request_opened", lambda payload: calls.__setitem__("opened", calls["opened"] + 1))
    monkeypatch.setattr(webhooks.update_environment, "delay", lambda **kw: calls.__setitem__("update", calls["update"] + 1))
    monkeypatch.setattr(webhooks.github_service, "update_pr_status", lambda **kw: calls.__setitem__("status", calls["status"] + 1))
    return calls


def test_push_with_no_environment_provisions_from_scratch(db_session, webhook_wiring):
    webhooks.handle_pull_request_synchronize(_payload())
    assert webhook_wiring == {"opened": 1, "update": 0, "status": 0}


def test_push_after_a_failed_preview_provisions_from_scratch(db_session, environment, webhook_wiring):
    environment_crud.update_environment_status(db_session, environment, EnvironmentStatus.FAILED, "ImagePullBackOff")
    webhooks.handle_pull_request_synchronize(_payload())
    assert webhook_wiring["opened"] == 1
    assert webhook_wiring["update"] == 0


def test_push_on_a_ready_preview_redeploys_in_place(db_session, environment, webhook_wiring):
    environment_crud.update_environment_status(db_session, environment, EnvironmentStatus.READY)
    webhooks.handle_pull_request_synchronize(_payload(sha="f" * 40))
    assert webhook_wiring["opened"] == 0
    assert webhook_wiring["update"] == 1
    db_session.refresh(environment)
    assert environment.commit_sha == "f" * 40
    assert environment.status == EnvironmentStatus.UPDATING
