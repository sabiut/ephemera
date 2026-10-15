"""
Previews from a CI/CD pipeline (docs/ci-pipeline.md, rollout step 1): the
model, POST/GET/DELETE /api/v1/previews, pipeline tokens, ordering, expiry,
the shared preview limit, and how a pipeline preview deploys and reports.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.tasks.environment as env_tasks
from app.config import settings
from app.models import APIToken, Environment, EnvironmentStatus
from app.services import lifecycle, pipeline, repo_access
from app.services.github import InstalledRepository
from app.services.provisioning import PreviewLimitReached

REPO = "acme/shop"
SHA = "a" * 40
SHA2 = "b" * 40
DIGEST = "ghcr.io/acme/shop@sha256:" + "1" * 64


@pytest.fixture()
def queued(monkeypatch):
    """Tasks the API queues, instead of running them."""
    calls = []
    for name in ("provision_environment", "update_environment", "destroy_environment"):
        task = getattr(env_tasks, name)
        monkeypatch.setattr(task, "delay", lambda _n=name, **kw: calls.append((_n, kw)))
    return calls


@pytest.fixture()
def github(monkeypatch):
    repos = [InstalledRepository(full_name="Acme/Shop", name="Shop", installation_id=5, private=False,
                                 default_branch="main", html_url="")]

    class FakeGitHub:
        def list_installed_repositories(self):
            return repos

        def is_collaborator(self, *a):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    yield repos
    repo_access.clear_cache()


def _req(user, name="pr-142", commit=SHA, **kw):
    return pipeline.PipelineRequest(repository_full_name="Acme/Shop", name=name, commit_sha=commit,
                                    installation_id=5, owner=user, ttl_hours=kw.pop("ttl_hours", 24), **kw)


# ------------------------------------------------------------------ validation

@pytest.mark.parametrize("field, value, words", [
    ("name", "PR_142", "name must be"), ("name", "-x", "name must be"), ("name", "a" * 21, "name must be"),
    ("commit_sha", "abc123", "full 40-character"), ("ttl_hours", 0, "between 1 and 168"),
    ("ttl_hours", 169, "between 1 and 168"),
])
def test_requests_that_cannot_work_are_refused(user, field, value, words):
    req = _req(user)
    setattr(req, field, value)
    with pytest.raises(pipeline.InvalidRequest, match=words):
        pipeline.validate(req)


def test_images_are_checked_and_tags_warned_about(user):
    assert pipeline.validate(_req(user, images={"web": DIGEST})) == []
    warnings = pipeline.validate(_req(user, images={"web": "ghcr.io/acme/shop:latest"}))
    assert "is a tag, not a digest" in warnings[0]
    with pytest.raises(pipeline.InvalidRequest, match="not an image reference"):
        pipeline.validate(_req(user, images={"web": "not an image"}))
    with pytest.raises(pipeline.InvalidRequest, match="not a compose service"):
        pipeline.validate(_req(user, images={"we b": DIGEST}))


# ------------------------------------------------------------------ create, update, order, limit

def test_a_request_creates_then_updates_the_named_preview(db_session, user, queued):
    env, action = pipeline.request_preview(db_session, _req(user, images={"web": DIGEST}, pull_request=142))
    assert action == "created" and env.source == "pipeline" and env.pr_number is None
    assert env.namespace.startswith("ci-pr-142-shop-") and env.preview_name == "pr-142"
    assert env.image_overrides == {"web": DIGEST} and env.linked_pr_number == 142
    assert env.environment_url == f"https://{env.namespace}.{settings.base_domain}"
    assert queued[-1][0] == "provision_environment" and queued[-1][1]["pr_number"] == 142

    env.status = EnvironmentStatus.READY
    db_session.commit()
    again, action = pipeline.request_preview(db_session, _req(user, commit=SHA2))
    assert action == "updated" and again.id == env.id and again.commit_sha == SHA2
    assert queued[-1][0] == "update_environment" and again.image_overrides is None

    again.status, again.removal_reason = EnvironmentStatus.DESTROYED, "stopped"
    db_session.commit()
    back, action = pipeline.request_preview(db_session, _req(user))
    assert action == "reprovisioned" and back.id == env.id and back.removal_reason is None
    assert db_session.query(Environment).count() == 1


def test_an_older_run_is_refused(db_session, user, queued):
    pipeline.request_preview(db_session, _req(user, serial=58))
    with pytest.raises(pipeline.OutOfOrder, match="serial 58"):
        pipeline.request_preview(db_session, _req(user, serial=57))
    pipeline.request_preview(db_session, _req(user, serial=59))   # newer: fine
    pipeline.request_preview(db_session, _req(user))              # no serial: not ordered


def test_pipeline_and_pull_request_previews_share_the_limit(db_session, user, queued, monkeypatch):
    monkeypatch.setattr(settings, "preview_max_active_per_repository", 2)
    db_session.add(Environment(repository_full_name="acme/shop", repository_name="shop", pr_number=7,
                               branch_name="b", commit_sha=SHA, namespace="pr-7-shop-x", installation_id=5,
                               owner_id=user.id, status=EnvironmentStatus.READY))
    db_session.commit()
    pipeline.request_preview(db_session, _req(user, name="one"))
    with pytest.raises(PreviewLimitReached) as e:
        pipeline.request_preview(db_session, _req(user, name="two"))
    assert "#7" in str(e.value) and "pipeline preview one" in str(e.value)
    pipeline.request_preview(db_session, _req(user, name="one", commit=SHA2))   # updating never counts itself


def test_expiry_follows_the_pipelines_deadline(db_session, user, queued):
    env, _ = pipeline.request_preview(db_session, _req(user, ttl_hours=2))
    deadline = lifecycle.expires_at(env)
    assert timedelta(hours=1, minutes=59) < deadline - datetime.now(timezone.utc) <= timedelta(hours=2)
    assert not lifecycle.is_idle(env) and lifecycle.is_idle(env, now=deadline + timedelta(seconds=1))


# ------------------------------------------------------------------ the API and pipeline tokens

def _token(db, user, scope=None, repository=None):
    raw = APIToken.generate_token()
    db.add(APIToken(user_id=user.id, token_hash=APIToken.hash_token(raw), token_prefix=raw[:8], name="ci",
                    scope=scope, repository_full_name=repository))
    db.commit()
    return {"Authorization": f"Bearer {raw}"}


BODY = {"repository": "acme/shop", "name": "pr-142", "commit": SHA, "images": {"web": DIGEST}}


def test_a_pipeline_token_deploys_its_repository_only(client, db_session, user, github, queued):
    headers = _token(db_session, user, "previews", "Acme/Shop")
    r = client.post("/api/v1/previews", json=BODY, headers=headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["action"] == "created" and body["name"] == "pr-142" and body["status"] == "pending"
    assert body["repository"] == "Acme/Shop"         # GitHub's spelling
    other = client.post("/api/v1/previews", json={**BODY, "repository": "acme/other"}, headers=headers)
    assert other.status_code == 403 and "for Acme/Shop" in other.json()["detail"]
    assert client.get("/api/v1/environments/", headers=headers).status_code == 403   # nothing but previews
    assert client.get("/api/v1/tokens/", headers=headers).status_code == 403


def test_the_preview_can_be_read_listed_and_deleted(client, db_session, user, github, queued, auth_headers):
    created = client.post("/api/v1/previews", json=BODY, headers=auth_headers).json()
    one = client.get(f"/api/v1/previews/{created['id']}", headers=auth_headers).json()
    assert one["name"] == "pr-142" and one["expires_at"]
    listed = client.get("/api/v1/previews?repository=acme/shop&name=pr-142", headers=auth_headers).json()
    assert [p["id"] for p in listed] == [created["id"]]
    env = db_session.get(Environment, created["id"])
    env.status = EnvironmentStatus.READY
    db_session.commit()
    gone = client.delete(f"/api/v1/previews/{created['id']}", headers=auth_headers)
    assert gone.status_code == 202 and queued[-1] == ("destroy_environment", {
        "environment_id": created["id"], "stopped": True, "stopped_by": "octocat"})


def test_errors_say_what_to_change(client, db_session, user, github, queued, auth_headers):
    assert client.post("/api/v1/previews", json={**BODY, "name": "Bad Name"}, headers=auth_headers).status_code == 422
    client.post("/api/v1/previews", json={**BODY, "serial": 9}, headers=auth_headers)
    older = client.post("/api/v1/previews", json={**BODY, "serial": 8}, headers=auth_headers)
    assert older.status_code == 409 and "newer run" in older.json()["detail"]
    missing = client.post("/api/v1/previews", json={**BODY, "repository": "acme/nope"}, headers=auth_headers)
    assert missing.status_code == 404
    tag = client.post("/api/v1/previews", json={**BODY, "name": "tagged", "images": {"web": "nginx:1"}},
                      headers=auth_headers)
    assert "is a tag, not a digest" in tag.json()["warnings"][0]


def test_a_failed_preview_carries_its_diagnosis(client, db_session, user, github, queued, auth_headers):
    created = client.post("/api/v1/previews", json=BODY, headers=auth_headers).json()
    env = db_session.get(Environment, created["id"])
    env.status, env.error_message = EnvironmentStatus.FAILED, "Services did not become ready: web (ImagePullBackOff: 401 Unauthorized)"
    db_session.commit()
    body = client.get(f"/api/v1/previews/{created['id']}", headers=auth_headers).json()
    assert body["status"] == "failed" and body["diagnosis"]["category"] == "image_private"


def test_pull_request_previews_are_not_previews_endpoints_business(client, db_session, user, github, auth_headers):
    env = Environment(repository_full_name="acme/shop", repository_name="shop", pr_number=7, branch_name="b",
                      commit_sha=SHA, namespace="pr-7-shop-x", installation_id=5, owner_id=user.id,
                      status=EnvironmentStatus.READY)
    db_session.add(env)
    db_session.commit()
    assert client.get(f"/api/v1/previews/{env.id}", headers=auth_headers).status_code == 404


def test_pipeline_tokens_are_created_for_a_repository(client, user, github, auth_headers):
    r = client.post("/api/v1/tokens/", json={"name": "ci", "repository": "acme/shop"}, headers=auth_headers)
    assert r.status_code == 201 and r.json()["scope"] == "previews" and r.json()["repository_full_name"] == "Acme/Shop"
    assert client.post("/api/v1/tokens/", json={"name": "x", "repository": "acme/nope"},
                       headers=auth_headers).status_code == 404


# ------------------------------------------------------------------ deploying and reporting

def test_a_pipeline_preview_deploys_the_images_it_was_sent(db_session, user, queued, monkeypatch):
    from tests.test_readiness import _deploy_result
    env, _ = pipeline.request_preview(db_session, _req(user, images={"web": DIGEST, "ghost": DIGEST},
                                                       compose="services:\n  web:\n    build: .\n"))
    calls = {}

    def deploy(**kwargs):
        calls.update(kwargs)
        return _deploy_result(services=["web"], service_urls={"web": "https://x"})
    monkeypatch.setattr(env_tasks.deployment_service, "deploy_application", deploy)
    monkeypatch.setattr(env_tasks.managed_builds, "build_commit", lambda *a, **k: pytest.fail("built"))
    monkeypatch.setattr(env_tasks.kubernetes_service, "sync_pull_secret", lambda *a: True)
    monkeypatch.setattr(env_tasks.kubernetes_service, "wait_for_deployments_ready", lambda *a, **k: (["web"], {}))
    monkeypatch.setattr(env_tasks, "check_readiness", lambda *a, **k: ({}, {}))
    result = env_tasks._run_deployment(db_session, env.id, 5, "Acme/Shop", env.namespace, SHA)
    assert calls["built_images"] == {"web": DIGEST, "ghost": DIGEST}
    assert calls["compose_content"] == "services:\n  web:\n    build: .\n"
    assert result["success"] and "doesn't have: ghost" in result["warnings"][0]


def test_statuses_are_reported_under_the_previews_own_name(db_session, user, queued, monkeypatch):
    env, _ = pipeline.request_preview(db_session, _req(user))
    assert env_tasks.status_context(env) == "ephemera/preview/pr-142"
    assert env_tasks.status_context(SimpleNamespace(source="pull_request")) == env_tasks.STATUS_CONTEXT
    posted = []
    monkeypatch.setattr(env_tasks.github_service, "update_pr_status", lambda **k: posted.append(k["context"]))
    token = env_tasks._STATUS.set(env_tasks.status_context(env))
    try:
        env_tasks._notify(5, "Acme/Shop", None, SHA, "success", "ready", None)
    finally:
        env_tasks._STATUS.reset(token)
    env_tasks._notify(5, "Acme/Shop", None, SHA, "success", "ready", None)
    assert posted == ["ephemera/preview/pr-142", "ephemera/environment"]


def test_closing_the_linked_pull_request_removes_it(db_session, user, queued, monkeypatch):
    import app.api.webhooks as webhooks
    from tests.test_onboarding_consistency import _Session
    env, _ = pipeline.request_preview(db_session, _req(user, pull_request=142))
    unlinked, _ = pipeline.request_preview(db_session, _req(user, name="nightly"))
    monkeypatch.setattr(webhooks, "SessionLocal", lambda: _Session(db_session))
    monkeypatch.setattr(webhooks.destroy_environment, "delay", lambda **kw: queued.append(("destroy", kw)))
    webhooks._remove_linked_pipeline_previews("acme/shop", 142)
    assert ("destroy", {"environment_id": env.id}) in queued
    assert not any(c == ("destroy", {"environment_id": unlinked.id}) for c in queued)
    assert db_session.get(Environment, env.id).closed_at is not None


def test_the_transient_retry_leaves_pipeline_previews_to_their_pipeline(db_session, user, queued, monkeypatch):
    from app.models import Deployment
    from app.tasks import cleanup
    env, _ = pipeline.request_preview(db_session, _req(user))
    env.status, env.error_message = EnvironmentStatus.FAILED, "Preview URLs did not answer: web (timed out)"
    db_session.commit()
    monkeypatch.setattr(cleanup, "SessionLocal", lambda: db_session, raising=False)
    task = cleanup.retry_failed_environments
    task._db = db_session
    try:
        result = task.run(max_age_hours=1)
    finally:
        task._db = None
    assert result["retry_count"] == 0 and result["skipped"] == 1
    assert db_session.query(Deployment).filter_by(environment_id=env.id).count() == 1


def test_usage_counts_pipeline_previews(client, db_session, user, github, queued, auth_headers):
    client.post("/api/v1/previews", json=BODY, headers=auth_headers)
    body = client.get("/api/v1/repositories/acme/shop/usage", headers=auth_headers).json()
    assert body["used"] == 1 and body["pipeline_previews"] == ["pr-142"] and body["pull_requests"] == []
