"""
Changing a repository's preview access takes effect on running previews
straight away, not at their next deploy, and the dashboard can tell when.
"""

from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException

import app.tasks.environment as env_tasks
from app.config import settings
from app.models import Environment, EnvironmentStatus, RepositorySettings
from app.services import preview_access, repo_access
from app.services.deployment import DeploymentService
from app.services.github import InstalledRepository

REPO = "acme/app"
NS = "pr-3-app-5f89da"


class FakeCluster:
    """Ingresses and Services in one preview namespace."""

    def __init__(self, hosts=("pr-3-app-5f89da-web.preview.test",), fail_patch=False):
        self.patches, self.deleted, self.created = [], [], []
        self.fail_patch = fail_patch
        rules = [SimpleNamespace(host=h) for h in hosts]
        self.ingresses = [SimpleNamespace(metadata=SimpleNamespace(name="web-ingress"), spec=SimpleNamespace(rules=rules)),
                          SimpleNamespace(metadata=SimpleNamespace(name=preview_access.AUTH_SERVICE),
                                          spec=SimpleNamespace(rules=[]))]

    def list_namespaced_ingress(self, namespace, label_selector):
        return SimpleNamespace(items=self.ingresses)

    def patch_namespaced_ingress(self, name, namespace, body):
        if self.fail_patch:
            raise ApiException(status=500)
        self.patches.append((name, body))

    def delete_namespaced_ingress(self, name, namespace):
        self.deleted.append(("Ingress", name))
        raise ApiException(status=404)  # already gone is fine

    def delete_namespaced_service(self, name, namespace):
        self.deleted.append(("Service", name))

    def __getattr__(self, attr):  # create/replace/patch of the sign-in route
        return lambda **kw: self.created.append((attr, kw.get("body", {}).get("kind")))


def _svc(cluster):
    k8s = SimpleNamespace(enabled=True, apps_v1=cluster, core_v1=cluster, networking_v1=cluster)
    return DeploymentService(k8s, github_service=None, base_domain="preview.test")


def test_protecting_a_running_preview_changes_its_routes_in_place():
    cluster = FakeCluster()
    assert _svc(cluster).apply_access(NS, True) is True
    name, body = cluster.patches[0]
    assert name == "web-ingress"  # never the sign-in route itself
    assert body["metadata"]["annotations"]["nginx.ingress.kubernetes.io/auth-url"].endswith("/preview-auth/check")
    kinds = {k for _, k in cluster.created}
    assert {"Service", "Ingress"} <= kinds  # the /_ephemera route


def test_opening_it_again_removes_the_annotations_and_the_route():
    cluster = FakeCluster()
    assert _svc(cluster).apply_access(NS, False) is True
    assert all(v is None for v in cluster.patches[0][1]["metadata"]["annotations"].values())  # null removes
    assert ("Ingress", preview_access.AUTH_SERVICE) in cluster.deleted
    assert ("Service", preview_access.AUTH_SERVICE) in cluster.deleted
    assert cluster.created == []


def test_a_failure_is_reported():
    assert _svc(FakeCluster(fail_patch=True)).apply_access(NS, True) is False


# ------------------------------------------------------------------ the task and the setting

@pytest.fixture()
def db(db_session, monkeypatch):
    monkeypatch.setattr(env_tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    return db_session


def _env(db, user, status=EnvironmentStatus.READY, pr=3, applied=None):
    e = Environment(repository_full_name=REPO, repository_name="app", pr_number=pr, pr_title="t", branch_name="b",
                    commit_sha="c" * 40, installation_id=1, owner_id=user.id, status=status,
                    namespace=f"pr-{pr}-app-5f89da", access_applied=applied)
    db.add(e)
    db.commit()
    return e


def _run(task, **kw):
    task._db = None
    try:
        return task.run(**kw)
    finally:
        task._db = None


def test_the_task_applies_the_setting_and_records_it(db, user, monkeypatch):
    env = _env(db, user)
    db.add(RepositorySettings(repository_full_name=REPO, protect_previews=True))
    db.commit()
    seen = []
    monkeypatch.setattr(env_tasks.deployment_service, "apply_access", lambda ns, protected: seen.append((ns, protected)) or True)
    assert _run(env_tasks.apply_preview_access, environment_id=env.id)["access"] == "protected"
    db.refresh(env)
    assert seen == [(env.namespace, True)] and env.access_applied == "protected"


def test_a_refused_change_is_retried_then_shown_as_failed(db, user, monkeypatch):
    from celery.exceptions import Retry
    env = _env(db, user, applied="public")
    db.add(RepositorySettings(repository_full_name=REPO, protect_previews=True))
    db.commit()
    monkeypatch.setattr(env_tasks.deployment_service, "apply_access", lambda ns, protected: False)
    retried = []
    monkeypatch.setattr(env_tasks.apply_preview_access, "retry",
                        lambda countdown, max_retries: retried.append(countdown) or Retry())
    with pytest.raises(Retry):
        _run(env_tasks.apply_preview_access, environment_id=env.id)
    db.refresh(env)
    assert retried == [10] and env.access_applied == "public"  # untouched while retrying

    monkeypatch.setattr(env_tasks, "_retries_so_far", lambda task: env_tasks.ACCESS_RETRIES)
    assert _run(env_tasks.apply_preview_access, environment_id=env.id)["success"] is False
    db.refresh(env)
    assert env.access_applied == "failed"  # visible, instead of stopping silently


def test_removed_previews_are_skipped(db, user, monkeypatch):
    env = _env(db, user, status=EnvironmentStatus.DESTROYED)
    monkeypatch.setattr(env_tasks.deployment_service, "apply_access", lambda *a: pytest.fail("must not touch the cluster"))
    assert _run(env_tasks.apply_preview_access, environment_id=env.id)["skipped"] == "not running"


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


def test_saving_the_setting_changes_running_previews_now(client, auth_headers, db, user, github, monkeypatch):
    a = _env(db, user, pr=3, applied="public")
    b = _env(db, user, pr=4, applied="protected")  # already right
    _env(db, user, pr=5, status=EnvironmentStatus.DESTROYED)
    queued = []
    monkeypatch.setattr(env_tasks.apply_preview_access, "delay", lambda **kw: queued.append(kw["environment_id"]))
    body = client.put(f"/api/v1/repositories/{REPO}/settings", headers=auth_headers, json={"protect_previews": True}).json()
    assert queued == [a.id]
    assert body["previews"] == 2 and body["previews_applied"] == 1  # "1 of 2 so far"
    a.access_applied = "protected"
    db.commit()
    done = client.get(f"/api/v1/repositories/{REPO}/settings", headers=auth_headers).json()
    assert done["previews_applied"] == done["previews"] == 2  # "in effect"


def test_a_deploy_records_the_access_it_applied(db, user, monkeypatch):
    from tests.test_readiness import wired  # noqa: F401
    env = _env(db, user)
    db.add(RepositorySettings(repository_full_name=REPO, protect_previews=True))
    db.commit()
    monkeypatch.setattr(env_tasks, "_active_deployment_service", lambda: SimpleNamespace(
        deploy_application=lambda **kw: {"success": False, "applied_count": 3, "error": "stop here"}))
    env_tasks._run_deployment(db, env.id, 1, REPO, env.namespace, "c" * 40)
    db.refresh(env)
    assert env.access_applied == "protected"
