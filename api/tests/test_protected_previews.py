"""
Protected previews: only the PR author, the repository's collaborators and
admins can open them, after signing in with GitHub. The cookie that lets a
viewer in is set for one preview host only, so no other customer's preview
app ever receives it.
"""

import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

import app.api.preview_auth as preview_auth_api
from app.config import settings
from app.core import signing
from app.models import Environment, EnvironmentStatus, RepositorySettings, User
from app.services import preview_access
from app.services.deployment import DeploymentService

REPO = "acme/app"
NS = "pr-3-app-5f89da"
HOST = f"{NS}-web.{settings.base_domain}"


@pytest.fixture()
def env(db_session, user):
    e = Environment(repository_full_name=REPO, repository_name="app", pr_number=3, pr_title="t", branch_name="b",
                    commit_sha="c" * 40, installation_id=1, owner_id=user.id, status=EnvironmentStatus.READY,
                    namespace=NS)
    db_session.add(e)
    db_session.commit()
    return e


@pytest.fixture()
def protected(db_session):
    db_session.add(RepositorySettings(repository_full_name=REPO, protect_previews=True))
    db_session.commit()


@pytest.fixture()
def stranger(db_session):
    u = User(github_id=999, github_login="stranger")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture(autouse=True)
def no_repo_access(monkeypatch):
    # Nobody is a collaborator unless a test says so; the PR author still is the owner.
    monkeypatch.setattr(preview_auth_api.repo_access, "accessible_repo_names", lambda user, admin: set())


def _check(client, cookie=None, probe=None, host=HOST):
    headers = {"X-Original-URL": f"https://{host}/some/page?x=1"}
    if probe:
        headers[preview_access.PROBE_HEADER] = probe
    client.cookies.clear()
    if cookie:
        client.cookies.set(preview_access.COOKIE, cookie)
    return client.get("/preview-auth/check", headers=headers).status_code


# ------------------------------------------------------------------ signing

def test_tokens_are_bound_to_their_purpose_and_expire():
    token = signing.sign("preview-cookie", {"ns": NS, "uid": 1}, 60)
    assert signing.unsign("preview-cookie", token)["ns"] == NS
    assert signing.unsign("preview-code", token) is None           # another purpose
    assert signing.unsign("preview-cookie", token[:-2] + "xx") is None  # tampered
    assert signing.unsign("preview-cookie", signing.sign("preview-cookie", {"ns": NS}, -1)) is None  # expired


def test_a_host_maps_to_its_preview(db_session, env):
    assert preview_access.environment_for_host(db_session, HOST).id == env.id
    assert preview_access.environment_for_host(db_session, HOST.upper() + ":443").id == env.id
    assert preview_access.environment_for_host(db_session, f"{NS}-web.evil.example") is None
    env.status = EnvironmentStatus.DESTROYED
    db_session.commit()
    assert preview_access.environment_for_host(db_session, HOST) is None


# ------------------------------------------------------------------ the check nginx makes

def test_an_unprotected_repositorys_previews_are_open(client, env):
    assert _check(client) == 200


def test_a_protected_preview_needs_a_cookie_for_itself(client, env, protected, user):
    assert _check(client) == 401
    assert _check(client, cookie=preview_access.mint_cookie(NS, user.id)) == 200            # the PR author
    assert _check(client, cookie=preview_access.mint_cookie("pr-9-other-abc123", user.id)) == 401  # another preview's
    assert _check(client, cookie="forged.cookie") == 401


def test_access_is_checked_again_not_just_the_cookie(client, env, protected, stranger):
    assert _check(client, cookie=preview_access.mint_cookie(NS, stranger.id)) == 401  # not a collaborator


def test_collaborators_get_in(client, env, protected, stranger, monkeypatch):
    monkeypatch.setattr(preview_auth_api.repo_access, "accessible_repo_names", lambda user, admin: {REPO})
    assert _check(client, cookie=preview_access.mint_cookie(NS, stranger.id)) == 200


def test_ephemeras_readiness_probe_is_let_through(client, env, protected):
    assert _check(client, probe=preview_access.probe_value(HOST)) == 200
    assert _check(client, probe=preview_access.probe_value("other-host.example")) == 401


# ------------------------------------------------------------------ sign-in

def test_start_signs_in_first(client, env, protected):
    r = client.get("/preview-auth/start", params={"rd": f"https://{HOST}/x"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/auth/github/login?next=")
    assert "%2Fpreview-auth%2Fstart" in r.headers["location"]


def test_start_refuses_people_without_access(client, env, protected, db_session, raw_token, user):
    env.owner_id = 12345  # not this user's PR
    db_session.commit()
    r = client.get("/preview-auth/start", params={"rd": f"https://{HOST}/"},
                   headers={"Authorization": f"Bearer {raw_token}"}, follow_redirects=False)
    assert r.status_code == 403 and "only the pull request" in r.text


def test_start_hands_the_preview_host_a_short_code(client, env, protected, raw_token, user):
    r = client.get("/preview-auth/start", params={"rd": f"https://{HOST}/page?q=1"},
                   headers={"Authorization": f"Bearer {raw_token}"}, follow_redirects=False)
    assert r.status_code == 303
    target = urlparse(r.headers["location"])
    assert target.netloc == HOST and target.path == preview_access.CALLBACK_PATH
    q = parse_qs(target.query)
    assert q["rd"] == ["/page?q=1"]
    claims = preview_access.read_code(q["code"][0])
    assert claims["ns"] == NS and claims["uid"] == user.id and claims["exp"] <= time.time() + preview_access.CODE_TTL


def test_start_for_an_unknown_link_explains(client):
    r = client.get("/preview-auth/start", params={"rd": "https://nowhere.example/"})
    assert r.status_code == 404 and "Preview not found" in r.text


def test_the_callback_sets_a_cookie_for_this_host_only(client, env, protected, user):
    code = preview_access.mint_code(NS, user.id)
    r = client.get(preview_access.CALLBACK_PATH, params={"code": code, "rd": "/page?q=1"},
                   headers={"Host": HOST}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/page?q=1"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{preview_access.COOKIE}=") and "HttpOnly" in cookie
    assert "domain=" not in cookie.lower()  # host-only: never sent to other previews


def test_the_callback_refuses_codes_for_other_previews_and_open_redirects(client, env, user):
    other = preview_access.mint_code("pr-9-other-abc123", user.id)
    assert client.get(preview_access.CALLBACK_PATH, params={"code": other}, headers={"Host": HOST}).status_code == 400
    code = preview_access.mint_code(NS, user.id)
    for rd in ("//evil.example/", "https://evil.example/", "/\\evil.example"):
        r = client.get(preview_access.CALLBACK_PATH, params={"code": code, "rd": rd}, headers={"Host": HOST},
                       follow_redirects=False)
        assert r.headers["location"] == "/"


def test_login_only_returns_to_paths_on_this_site():
    from app.api.auth import _safe_next
    assert _safe_next("/preview-auth/start?rd=x") == "/preview-auth/start?rd=x"
    assert _safe_next("//evil.example") is None and _safe_next("https://evil.example") is None
    assert _safe_next("/\\evil.example") is None


# ------------------------------------------------------------------ deploys

def _deploy(protected_ns):
    applied = []

    class Api:
        def __getattr__(self, attr):
            return lambda **kw: applied.append((attr, kw.get("body")))

    k8s = SimpleNamespace(enabled=True, apps_v1=Api(), core_v1=Api(), networking_v1=Api())
    svc = DeploymentService(k8s, github_service=None, base_domain=settings.base_domain)
    svc.prune_obsolete = lambda ns, manifests: applied.append(("prune", [m["metadata"]["name"] for m in manifests])) or []
    svc.set_protection(NS, protected_ns)
    manifests = svc.convert_compose_to_k8s({"services": {"web": {"image": "nginx", "ports": ["80:80"]}}}, NS, "app")
    svc.apply_manifests(manifests)
    return applied


def test_a_protected_preview_is_deployed_behind_sign_in():
    applied = _deploy(True)
    ingresses = [b for a, b in applied if isinstance(b, dict) and b.get("kind") == "Ingress"]
    app_ing = next(i for i in ingresses if i["metadata"]["name"] != preview_access.AUTH_SERVICE)
    auth_ing = next(i for i in ingresses if i["metadata"]["name"] == preview_access.AUTH_SERVICE)
    assert app_ing["metadata"]["annotations"]["nginx.ingress.kubernetes.io/auth-url"].endswith("/preview-auth/check")
    assert "auth-url" not in str(auth_ing["metadata"].get("annotations", {}))  # the callback route stays open
    assert auth_ing["spec"]["rules"][0]["host"] == app_ing["spec"]["rules"][0]["host"]
    svc = next(b for a, b in applied if isinstance(b, dict) and b.get("kind") == "Service"
               and b["metadata"]["name"] == preview_access.AUTH_SERVICE)
    assert svc["spec"]["type"] == "ExternalName"
    assert preview_access.AUTH_SERVICE in next(b for a, b in applied if a == "prune")  # kept by pruning


def test_an_open_preview_has_no_sign_in_and_loses_the_route():
    applied = _deploy(False)
    assert not any(isinstance(b, dict) and "auth-url" in str(b.get("metadata", {}).get("annotations", {}))
                   for a, b in applied)
    assert preview_access.AUTH_SERVICE not in next(b for a, b in applied if a == "prune")  # pruned if it existed


def test_ingress_updates_replace_rather_than_merge(monkeypatch):
    from kubernetes.client.rest import ApiException
    calls = []

    class Api:
        def create_namespaced_ingress(self, namespace, body):
            raise ApiException(status=409)

        def replace_namespaced_ingress(self, name, namespace, body):
            calls.append("replace")

        def patch_namespaced_ingress(self, name, namespace, body):
            calls.append("patch")

        def __getattr__(self, attr):  # the other kinds' calls, unused here
            return lambda **kw: None

    k8s = SimpleNamespace(enabled=True, apps_v1=Api(), core_v1=Api(), networking_v1=Api())
    svc = DeploymentService(k8s, github_service=None, base_domain=settings.base_domain)
    svc.apply_manifest({"kind": "Ingress", "metadata": {"name": "web-ingress", "namespace": NS}, "spec": {}})
    assert calls == ["replace"]  # a merge would keep auth annotations after protection is turned off


def test_the_readiness_probe_proves_itself(monkeypatch):
    import httpx
    from app.services.deployment import probe_urls
    seen = {}

    def get(url, timeout, follow_redirects, headers):
        seen.update(headers)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(httpx, "get", get)
    assert probe_urls({"web": f"https://{HOST}"}, timeout_seconds=1) == {}
    assert seen[preview_access.PROBE_HEADER] == preview_access.probe_value(HOST)


# ------------------------------------------------------------------ the setting

def test_collaborators_switch_protection_per_repository(client, auth_headers, monkeypatch):
    from app.services import repo_access
    from app.services.github import InstalledRepository

    class FakeGitHub:
        def list_installed_repositories(self):
            return [InstalledRepository(REPO, "app", 1, True, "main", "")]

        def is_collaborator(self, *a):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    assert client.get(f"/api/v1/repositories/{REPO}/settings", headers=auth_headers).json()["protect_previews"] is False
    r = client.put(f"/api/v1/repositories/{REPO}/settings", headers=auth_headers, json={"protect_previews": True})
    assert r.json()["protect_previews"] is True and r.json()["updated_by_login"]
    assert client.get(f"/api/v1/repositories/{REPO}/settings", headers=auth_headers).json()["protect_previews"] is True
    assert client.put("/api/v1/repositories/other/secret/settings", headers=auth_headers,
                      json={"protect_previews": True}).status_code == 404
    repo_access.clear_cache()
