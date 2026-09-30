"""
Regression tests for the review of #57-60: access is only claimed when it is
really in effect, readiness trusts only the latest answer, and four security
gaps (registry-secret mounts, standing probe bypass, repository-name casing,
disabled users) are closed.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

import app.api.preview_auth as preview_auth_api
import app.tasks.environment as env_tasks
from app.config import settings
from app.core import signing
from app.crud import environment as environment_crud
from app.models import Environment, EnvironmentStatus, RepositorySettings, User
from app.services import preview_access, registries
from app.services.deployment import DeploymentService, check_readiness, harden_deployment
from app.services.provisioning import EnvironmentRequest, PreviewLimitReached, request_environment
from tests.test_readiness import environment, wired  # noqa: F401 (fixtures)

NS = "pr-3-app-5f89da"
HOST = f"{NS}-web.{settings.base_domain}"


def _env(db, user, repo="acme/app", pr=3, status=EnvironmentStatus.READY, ns=NS):
    e = Environment(repository_full_name=repo, repository_name="app", pr_number=pr, pr_title="t", branch_name="b",
                    commit_sha="c" * 40, installation_id=1, owner_id=user.id, status=status, namespace=ns)
    db.add(e)
    db.commit()
    return e


# ------------------------------------------------------------------ 1. access recorded only when in effect

@pytest.mark.parametrize("failed,expected", [
    ([], "protected"),
    (["Deployment/web"], "protected"),            # routes applied; the app itself failed
    (["Ingress/web-ingress"], "failed"),          # the review's case: the public route may be unchanged
    ([f"Service/{preview_access.AUTH_SERVICE}"], "failed"),
])
def test_a_deploy_claims_protection_only_when_every_route_applied(db_session, user, monkeypatch, failed, expected):
    env = _env(db_session, user)
    db_session.add(RepositorySettings(repository_full_name="acme/app", protect_previews=True))
    db_session.commit()
    monkeypatch.setattr(env_tasks, "_active_deployment_service", lambda: SimpleNamespace(
        deploy_application=lambda **kw: {"success": not failed, "applied_count": 3, "failed_manifests": failed,
                                         "error": "x" if failed else None}))
    env_tasks._run_deployment(db_session, env.id, 1, "acme/app", env.namespace, "c" * 40)
    db_session.refresh(env)
    assert env.access_applied == expected


def test_failed_changes_are_counted_and_retried_on_save(client, auth_headers, db_session, user, monkeypatch):
    from app.services import repo_access
    from app.services.github import InstalledRepository

    class FakeGitHub:
        def list_installed_repositories(self):
            return [InstalledRepository("acme/app", "app", 1, True, "main", "")]

        def is_collaborator(self, *a):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    env = _env(db_session, user)
    env.access_applied = "failed"
    db_session.add(RepositorySettings(repository_full_name="acme/app", protect_previews=True))
    db_session.commit()
    body = client.get("/api/v1/repositories/acme/app/settings", headers=auth_headers).json()
    assert body["previews_failed"] == 1 and body["previews_applied"] == 0
    queued = []
    monkeypatch.setattr(env_tasks.apply_preview_access, "delay", lambda **kw: queued.append(kw["environment_id"]))
    client.put("/api/v1/repositories/acme/app/settings", headers=auth_headers, json={"protect_previews": True})
    assert queued == [env.id]  # Try again
    repo_access.clear_cache()


# ------------------------------------------------------------------ 3. readiness trusts the latest answer

def _sequence(monkeypatch, answers):
    seq = list(answers)

    def get(url, timeout, follow_redirects, headers):
        a = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(status_code=a)

    monkeypatch.setattr(httpx, "get", get)


def test_a_404_followed_by_a_503_is_a_failure(monkeypatch):
    _sequence(monkeypatch, [404, 503])
    failures, readiness = check_readiness({"web": "https://web"}, {}, timeout_seconds=0.05, poll_seconds=0,
                                          unverified_grace_seconds=60)
    assert failures == {"web": "HTTP 503 at /"} and readiness == {}  # was: accepted as "responding"


def test_a_404_followed_by_a_dropped_connection_fails_without_crashing(monkeypatch):
    _sequence(monkeypatch, [404, httpx.ConnectError("connection refused")])
    failures, readiness = check_readiness({"web": "https://web"}, {}, timeout_seconds=0.05, poll_seconds=0,
                                          unverified_grace_seconds=60)
    assert failures["web"].startswith("ConnectError")  # was: ValueError parsing the error as a status


def test_a_steady_404_on_root_is_still_responding_at_the_timeout(monkeypatch):
    _sequence(monkeypatch, [404])
    failures, readiness = check_readiness({"web": "https://web"}, {}, timeout_seconds=0.05, poll_seconds=0,
                                          unverified_grace_seconds=60)
    assert failures == {} and readiness["web"] == {"path": "/", "status": 404, "verified": False}


# ------------------------------------------------------------------ 4. the registry Secret stays with the kubelet

def _dep(**pod):
    return {"kind": "Deployment", "metadata": {"name": "web", "namespace": NS},
            "spec": {"template": {"spec": {"containers": [{"name": "web", "image": "nginx", **pod.pop("container", {})}], **pod}}}}


@pytest.mark.parametrize("manifest", [
    _dep(volumes=[{"name": "r", "secret": {"secretName": "ephemera-registry"}}]),
    _dep(volumes=[{"name": "p", "projected": {"sources": [{"secret": {"name": "ephemera-registry"}}]}}]),
    _dep(container={"env": [{"name": "T", "valueFrom": {"secretKeyRef": {"name": "ephemera-registry", "key": ".dockerconfigjson"}}}]}),
    _dep(container={"envFrom": [{"secretRef": {"name": "ephemera-registry"}}]}),
])
def test_no_container_may_read_the_registry_token(manifest):
    assert "registry credentials" in harden_deployment(manifest)


def test_other_secrets_are_still_allowed():
    assert harden_deployment(_dep(volumes=[{"name": "s", "secret": {"secretName": "app-config"}}])) is None


def test_a_manifest_cannot_overwrite_the_registry_secret():
    calls = []
    api = SimpleNamespace(**{n: (lambda **kw: calls.append(kw)) for n in (
        "create_namespaced_secret", "patch_namespaced_secret")})
    k8s = SimpleNamespace(enabled=True, apps_v1=api, core_v1=api, networking_v1=api)
    svc = DeploymentService(k8s, None, "preview.test")
    ok = svc.apply_manifest({"kind": "Secret", "metadata": {"name": "ephemera-registry", "namespace": NS}, "data": {}})
    assert ok is False and calls == []


# ------------------------------------------------------------------ 5. the probe pass expires

def test_the_probe_pass_is_short_lived_and_host_bound():
    assert preview_access.probe_valid(preview_access.probe_value(HOST), HOST)
    assert not preview_access.probe_valid(preview_access.probe_value(HOST), "other." + settings.base_domain)
    expired = signing.sign("preview-probe", {"h": HOST}, -1)
    assert not preview_access.probe_valid(expired, HOST)  # was: a fixed value that worked forever


def test_an_old_probe_pass_is_refused_by_the_check(client, db_session, user):
    _env(db_session, user)
    db_session.add(RepositorySettings(repository_full_name="acme/app", protect_previews=True))
    db_session.commit()
    old = signing.sign("preview-probe", {"h": HOST}, -1)
    r = client.get("/preview-auth/check", headers={"X-Original-URL": f"https://{HOST}/", preview_access.PROBE_HEADER: old})
    assert r.status_code == 401


# ------------------------------------------------------------------ 6. repository names ignore case

def test_protection_applies_whatever_the_casing(db_session, user):
    db_session.add(RepositorySettings(repository_full_name="acme/app", protect_previews=True))
    db_session.commit()
    assert preview_access.is_protected(db_session, "Acme/App")  # was: public


def test_registry_tokens_are_found_whatever_the_casing(db_session):
    registries.upsert(db_session, "acme/app", "ghcr.io", "u", "t", created_by=None)
    assert len(registries.credentials_for(db_session, "ACME/App")) == 1


def test_the_limit_counts_every_casing_as_one_repository(db_session, user, monkeypatch):
    monkeypatch.setattr(settings, "preview_max_active_per_repository", 1)
    _env(db_session, user, repo="Acme/App", pr=10, ns="pr-10-app-aaaaaa")
    with pytest.raises(PreviewLimitReached):
        request_environment(db_session, EnvironmentRequest("acme/app", "app", 3, "t", "b", "c" * 40, 1, user))


def test_lookups_by_pr_and_collaborator_visibility_ignore_case(db_session, user):
    env = _env(db_session, user, repo="Acme/App")
    assert environment_crud.get_environment_by_pr(db_session, "acme/app", 3).id == env.id
    other = User(github_id=77, github_login="reviewer")
    db_session.add(other)
    db_session.commit()
    visible = environment_crud.visible_environments(db_session, other, False, repo_names={"acme/app"}).all()
    assert [e.id for e in visible] == [env.id]


def test_the_api_stores_githubs_spelling(client, auth_headers, db_session, monkeypatch):
    import app.api.environments as environments_module
    from app.services.github import PullRequestInfo
    from tests.test_environment_create import FakeGitHub
    fake = FakeGitHub(collaborator=True)
    fake.pr = PullRequestInfo(7, "t", "open", "f" * 40, "b", 99, "contributor", None, repository_full_name="acme/app")
    monkeypatch.setattr(environments_module, "github_service", fake)
    captured = {}
    monkeypatch.setattr(environments_module, "request_environment",
                        lambda db, req: (captured.setdefault("repo", req.repository_full_name),
                                         (_env(db_session, User(id=1), repo=req.repository_full_name), "created"))[1])
    client.post("/api/v1/environments/", json={"repository_full_name": "ACME/APP", "pr_number": 7}, headers=auth_headers)
    assert captured["repo"] == "acme/app"


# ------------------------------------------------------------------ 7. disabled users

def test_a_disabled_user_loses_preview_access_at_once(client, db_session, user):
    _env(db_session, user)
    db_session.add(RepositorySettings(repository_full_name="acme/app", protect_previews=True))
    db_session.commit()
    cookie = preview_access.mint_cookie(NS, user.id)
    client.cookies.set(preview_access.COOKIE, cookie)
    assert client.get("/preview-auth/check", headers={"X-Original-URL": f"https://{HOST}/"}).status_code == 200
    user.is_active = False
    db_session.commit()
    assert client.get("/preview-auth/check", headers={"X-Original-URL": f"https://{HOST}/"}).status_code == 401
    client.cookies.clear()


def test_a_disabled_account_is_told_instead_of_looping_through_sign_in(client, db_session, monkeypatch):
    from app.main import app
    from app.services.auth import get_github_oauth_service
    disabled = User(github_id=555, github_login="gone", is_active=False)
    db_session.add(disabled)
    db_session.commit()

    class FakeOAuth:
        async def exchange_code_for_token(self, code):
            return "t"

        async def get_github_user(self, token):
            return {"id": 555, "login": "gone"}

        def create_or_update_user(self, db, github_user):
            return disabled

        def create_session_token(self, db, user):
            return "session"

    app.dependency_overrides[get_github_oauth_service] = lambda: FakeOAuth()
    client.cookies.set("ephemera_oauth_state", "s")
    r = client.get("/auth/github/callback", params={"code": "c", "state": "s"}, follow_redirects=False)
    app.dependency_overrides.pop(get_github_oauth_service)
    client.cookies.clear()
    assert r.status_code == 403 and "disabled" in r.text
    assert "ephemera_session" not in r.headers.get("set-cookie", "")
