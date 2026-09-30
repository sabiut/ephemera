"""
"Ready" means the preview works: with ephemera.readiness-path the path must
answer 2xx/3xx; without it, a 4xx on "/" is reported as responding but not
verified instead of being passed off as ready.
"""

from types import SimpleNamespace

import pytest

import app.tasks.environment as env_tasks
from app.services.compose import readiness_path
from app.services.deployment import DeploymentService, check_readiness
from app.services.diagnosis import explain
from app.services.github import InstalledRepository
from app.services import setup_check
from tests.test_readiness import environment, wired  # noqa: F401 (fixtures)


@pytest.mark.parametrize("labels,expected", [
    ({"ephemera.readiness-path": "/health"}, "/health"),
    (["ephemera.readiness-path=/api/ready"], "/api/ready"),
    ({"ephemera.readiness-path": "health"}, None),       # must be a path
    ({"ephemera.readiness-path": "//evil.example"}, None),
    ({}, None),
])
def test_the_label(labels, expected):
    assert readiness_path({"labels": labels}) == expected


def _fake_http(monkeypatch, answers):
    """answers: {url: [status, status, ...]} consumed in order (last one repeats)."""
    import httpx
    asked = []

    def get(url, timeout, follow_redirects, headers):
        asked.append(url)
        seq = answers[url]
        code = seq.pop(0) if len(seq) > 1 else seq[0]
        return SimpleNamespace(status_code=code)

    monkeypatch.setattr(httpx, "get", get)
    return asked


def test_a_readiness_path_must_answer_2xx(monkeypatch):
    asked = _fake_http(monkeypatch, {"https://web/health": [200]})
    failures, readiness = check_readiness({"web": "https://web"}, {"web": "/health"}, timeout_seconds=1, poll_seconds=0)
    assert failures == {} and readiness["web"] == {"path": "/health", "status": 200, "verified": True}
    assert asked == ["https://web/health"]


def test_a_readiness_path_that_keeps_failing_fails_the_preview(monkeypatch):
    _fake_http(monkeypatch, {"https://web/health": [404]})
    failures, readiness = check_readiness({"web": "https://web"}, {"web": "/health"}, timeout_seconds=0.05, poll_seconds=0)
    assert failures == {"web": "HTTP 404 at /health"} and readiness == {}


def test_without_a_path_a_404_on_root_is_responding_not_verified(monkeypatch):
    _fake_http(monkeypatch, {"https://api/": [404]})
    failures, readiness = check_readiness({"api": "https://api"}, {}, timeout_seconds=1, poll_seconds=0,
                                          unverified_grace_seconds=0)
    assert failures == {}  # an API with no "/" route still deploys
    assert readiness["api"] == {"path": "/", "status": 404, "verified": False}


def test_the_default_backends_404_is_not_mistaken_for_the_app(monkeypatch):
    # ingress-nginx answers 404 until the route is live; the app then says 200.
    _fake_http(monkeypatch, {"https://web/": [404, 404, 200]})
    failures, readiness = check_readiness({"web": "https://web"}, {}, timeout_seconds=5, poll_seconds=0,
                                          unverified_grace_seconds=60)
    assert readiness["web"]["verified"] is True and readiness["web"]["status"] == 200


def test_server_errors_still_fail(monkeypatch):
    _fake_http(monkeypatch, {"https://web/": [503]})
    failures, readiness = check_readiness({"web": "https://web"}, {}, timeout_seconds=0.05, poll_seconds=0)
    assert failures == {"web": "HTTP 503 at /"}


def test_deploy_results_carry_each_services_readiness_path():
    class Api:
        def __getattr__(self, attr):
            return lambda **kw: None

    svc = DeploymentService(SimpleNamespace(enabled=True, apps_v1=Api(), core_v1=Api(), networking_v1=Api()), None, "preview.test")
    svc.fetch_docker_compose = lambda *a, **k: (
        "services:\n  web:\n    image: nginx\n    ports: ['80']\n    labels:\n      ephemera.readiness-path: /health\n")
    svc.apply_manifests = lambda manifests, revision=None: (len(manifests), [], {"web": "https://web"})
    result = svc.deploy_application(1, "acme/app", "pr-1-app", ref="a" * 40)
    assert result["readiness_paths"] == {"web": "/health"}


def test_an_unverified_preview_says_so_on_the_commit_and_the_pr(db_session, environment, wired):
    wired["readiness"] = {"web": {"path": "/", "status": 404, "verified": False}}
    result = env_tasks._run_deployment(db_session, environment.id, 1, "acme/app", environment.namespace, "c" * 40)
    assert result["success"] is True
    db_session.refresh(environment)
    assert environment.readiness["web"]["verified"] is False
    assert env_tasks._ready_description(result, "Preview environment ready") == "Deployed, not verified: web / returned 404"
    summary = env_tasks._deployment_summary(result)
    assert "**Checks**" in summary and "ephemera.readiness-path: /health" in summary


def test_a_verified_preview_is_plainly_ready():
    result = {"readiness": {"web": {"path": "/health", "status": 200, "verified": True}}}
    assert env_tasks._ready_description(result, "Preview environment ready") == "Preview environment ready"
    assert "`/health` answered 200" in env_tasks._deployment_summary(result)


def test_a_failed_readiness_path_is_explained():
    d = explain("Preview URLs did not answer: web (HTTP 404 at /health)", "acme/app", "a" * 40, 3)
    assert d.category == "readiness_failed" and "ephemera.readiness-path" in d.action


def test_the_setup_check_suggests_a_readiness_path():
    repo = InstalledRepository("acme/app", "app", 1, False, "main", "")
    without = setup_check.check_repository(repo, fetch=lambda r, ref: ("docker-compose.yml", "services:\n  web:\n    image: nginx\n    ports: ['80']\n"))
    assert any(c.title == "web: add a readiness path (optional)" for c in without.checks)
    assert without.ready is True  # a suggestion, not an error
    with_label = setup_check.check_repository(repo, fetch=lambda r, ref: (
        "docker-compose.yml", "services:\n  web:\n    image: nginx\n    ports: ['80']\n    labels:\n      ephemera.readiness-path: /health\n"))
    assert not any("readiness path" in c.title for c in with_label.checks)
