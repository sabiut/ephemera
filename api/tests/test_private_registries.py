"""
Previews can pull private images: a repository's read-only registry tokens
become the preview's image pull Secret, referenced by every Deployment. A
pull refused for lack of access fails at once with the fix, instead of
waiting ten minutes as if CI were still building.
"""

import base64
import json
from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException

import app.tasks.environment as env_tasks
from app.models import RegistryCredential
from app.services import registries, repo_access
from app.services.deployment import DeploymentService
from app.services.diagnosis import explain
from app.services.github import InstalledRepository
from app.services.kubernetes import KubernetesService
from tests.test_readiness import _k8s, _pod, environment, wired  # noqa: F401 (fixtures)

REPO = "acme/app"
SHA = "a" * 40


# ------------------------------------------------------------------ registry names

@pytest.mark.parametrize("given,expected", [
    ("ghcr.io", "ghcr.io"), ("https://GHCR.io/", "ghcr.io"), ("us-docker.pkg.dev", "us-docker.pkg.dev"),
    ("docker.io", registries.DOCKER_HUB), ("index.docker.io", registries.DOCKER_HUB),
    ("registry.example.com:5000", "registry.example.com:5000"),
])
def test_registry_hosts_are_normalised(given, expected):
    assert registries.normalize_registry(given) == expected


@pytest.mark.parametrize("bad", ["", "not a host", "ghcr", "https://"])
def test_invalid_registries_are_refused(bad):
    with pytest.raises(registries.InvalidRegistry):
        registries.normalize_registry(bad)


@pytest.mark.parametrize("image,registry", [
    ("nginx", registries.DOCKER_HUB), ("library/nginx:1", registries.DOCKER_HUB),
    ("acme/web:latest", registries.DOCKER_HUB), ("ghcr.io/acme/web:abc", "ghcr.io"),
    ("us-docker.pkg.dev/p/r/web", "us-docker.pkg.dev"), ("localhost:5000/web", "localhost:5000"),
])
def test_the_registry_of_an_image(image, registry):
    assert registries.image_registry(image) == registry


# ------------------------------------------------------------------ storage

def test_tokens_are_encrypted_and_replaced_per_registry(db_session):
    first = registries.upsert(db_session, REPO, "ghcr.io", "octocat", "ghp_secret1", created_by="octocat")
    assert "ghp_secret1" not in first.secret_encrypted
    again = registries.upsert(db_session, REPO, "https://ghcr.io/", "octocat", "ghp_secret2", created_by="hubot")
    assert again.id == first.id and db_session.query(RegistryCredential).count() == 1
    cfg = json.loads(registries.docker_config(registries.credentials_for(db_session, REPO)))
    entry = cfg["auths"]["ghcr.io"]
    assert entry["password"] == "ghp_secret2"
    assert base64.b64decode(entry["auth"]).decode() == "octocat:ghp_secret2"


def test_no_credentials_means_no_pull_secret(db_session):
    assert registries.docker_config([]) is None


# ------------------------------------------------------------------ API

@pytest.fixture()
def github(monkeypatch):
    class FakeGitHub:
        def list_installed_repositories(self):
            return [InstalledRepository(REPO, "app", 1, True, "main", "")]

        def is_collaborator(self, *a):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    yield
    repo_access.clear_cache()


def test_the_api_never_returns_a_token(client, auth_headers, github):
    r = client.put(f"/api/v1/repositories/{REPO}/registries", headers=auth_headers,
                   json={"registry": "ghcr.io", "username": "octocat", "token": "ghp_topsecret"})
    assert r.status_code == 200 and "ghp_topsecret" not in r.text
    listed = client.get(f"/api/v1/repositories/{REPO}/registries", headers=auth_headers)
    assert "ghp_topsecret" not in listed.text
    assert listed.json()[0]["registry"] == "ghcr.io" and listed.json()[0]["created_by_login"]


def test_bad_input_and_other_repositories_are_refused(client, auth_headers, github):
    bad = client.put(f"/api/v1/repositories/{REPO}/registries", headers=auth_headers,
                     json={"registry": "not a host", "username": "u", "token": "t"})
    assert bad.status_code == 422
    assert client.get("/api/v1/repositories/other/secret/registries", headers=auth_headers).status_code == 404


def test_a_token_can_be_removed(client, auth_headers, github):
    created = client.put(f"/api/v1/repositories/{REPO}/registries", headers=auth_headers,
                         json={"registry": "docker.io", "username": "u", "token": "t"}).json()
    assert created["registry"] == "docker.io"  # shown the way users write it
    assert client.delete(f"/api/v1/repositories/{REPO}/registries/{created['id']}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/v1/repositories/{REPO}/registries", headers=auth_headers).json() == []
    assert client.delete(f"/api/v1/repositories/{REPO}/registries/{created['id']}", headers=auth_headers).status_code == 404


# ------------------------------------------------------------------ the pull Secret

class FakeCore:
    def __init__(self, exists=False, missing_on_delete=False):
        self.calls, self.exists, self.missing = [], exists, missing_on_delete

    def create_namespaced_secret(self, namespace, body):
        self.calls.append(("create", body))
        if self.exists:
            raise ApiException(status=409)

    def patch_namespaced_secret(self, name, namespace, body):
        self.calls.append(("patch", body))

    def delete_namespaced_secret(self, name, namespace):
        self.calls.append(("delete", name))
        if self.missing:
            raise ApiException(status=404)


def _k8s_with(core):
    svc = KubernetesService.__new__(KubernetesService)
    svc.enabled, svc.core_v1 = True, core
    return svc


def test_the_pull_secret_is_created_updated_and_removed():
    config = json.dumps({"auths": {"ghcr.io": {"auth": "x"}}})
    core = FakeCore()
    assert _k8s_with(core).sync_pull_secret("pr-1-app", config) is True
    kind, body = core.calls[0]
    assert kind == "create" and body["type"] == "kubernetes.io/dockerconfigjson"
    assert json.loads(base64.b64decode(body["data"][".dockerconfigjson"])) == json.loads(config)

    core = FakeCore(exists=True)
    assert _k8s_with(core).sync_pull_secret("pr-1-app", config) is True
    assert [c[0] for c in core.calls] == ["create", "patch"]

    core = FakeCore(missing_on_delete=True)
    assert _k8s_with(core).sync_pull_secret("pr-1-app", None) is True  # nothing to remove is fine
    assert core.calls == [("delete", "ephemera-registry")]


def test_every_deployment_references_the_pull_secret():
    applied = []

    class Api:
        def __getattr__(self, attr):
            return lambda **kw: applied.append(kw["body"])

    k8s = SimpleNamespace(enabled=True, apps_v1=Api(), core_v1=Api(), networking_v1=Api())
    svc = DeploymentService(k8s, github_service=None, base_domain="preview.test")
    dep = next(m for m in svc.convert_compose_to_k8s({"services": {"web": {"image": "ghcr.io/acme/web:x", "ports": ["80"]}}},
                                                     "pr-1-app", "app") if m["kind"] == "Deployment")
    svc.set_pull_secret("pr-1-app", "ephemera-registry")
    assert svc.apply_manifest(dep) is True
    assert applied[0]["spec"]["template"]["spec"]["imagePullSecrets"] == [{"name": "ephemera-registry"}]
    svc.set_pull_secret("pr-1-app", None)
    applied.clear()
    assert svc.apply_manifest(dep) is True
    assert applied[0]["spec"]["template"]["spec"]["imagePullSecrets"] == [{"name": "ephemera-registry"}]  # already on the dict
    fresh = next(m for m in svc.convert_compose_to_k8s({"services": {"web": {"image": "nginx", "ports": ["80"]}}},
                                                       "pr-1-app", "app") if m["kind"] == "Deployment")
    svc.apply_manifest(fresh)
    assert "imagePullSecrets" not in applied[-1]["spec"]["template"]["spec"]


def test_a_deploy_syncs_the_repositorys_credentials_into_the_namespace(db_session, environment, wired, monkeypatch):
    synced = []
    monkeypatch.setattr(env_tasks.kubernetes_service, "sync_pull_secret",
                        lambda ns, config, name="ephemera-registry": synced.append((ns, config)) or True)
    registries.upsert(db_session, environment.repository_full_name, "ghcr.io", "octocat", "ghp_x", created_by=None)
    env_tasks._run_deployment(db_session, environment.id, 1, environment.repository_full_name, environment.namespace, SHA)
    assert synced[0][0] == environment.namespace and "ghcr.io" in synced[0][1]
    assert env_tasks.deployment_service.pull_secrets.get(environment.namespace) == "ephemera-registry"


def test_a_deploy_fails_rather_than_run_without_its_credentials(db_session, environment, wired, monkeypatch):
    monkeypatch.setattr(env_tasks.kubernetes_service, "sync_pull_secret", lambda ns, config, name="ephemera-registry": False)
    registries.upsert(db_session, environment.repository_full_name, "ghcr.io", "octocat", "ghp_x", created_by=None)
    with pytest.raises(RuntimeError, match="registry credentials"):
        env_tasks._run_deployment(db_session, environment.id, 1, environment.repository_full_name, environment.namespace, SHA)


# ------------------------------------------------------------------ failing fast

def test_a_denied_pull_fails_at_once_with_the_fix():
    k8s = _k8s({"web": {"ready": 0}}, pods=[_pod(
        "ErrImagePull", 'failed to pull and unpack image "ghcr.io/acme/web:%s": failed to authorize: 401 Unauthorized' % SHA,
        phase="Pending", image=f"ghcr.io/acme/web:{SHA}")])
    ready, problems = k8s.wait_for_deployments_ready(
        "ns", ["web"], timeout_seconds=0, poll_seconds=0, image_wait_seconds=600, commit_markers=(SHA, SHA[:7]))
    assert ready == [] and "(access denied)" in problems["web"]  # not a ten-minute wait for CI
    d = explain("Services did not become ready: web (%s)" % problems["web"], REPO, SHA, 3)
    assert d.category == "image_private" and "read-only registry token" in d.action
