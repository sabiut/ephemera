"""
Users manage their previews within the limit: stop one while the PR stays
open, recreate it later, see how many slots are used, and keep an idle one
from expiring.
"""

from datetime import datetime, timedelta, timezone

import pytest

import app.api.environments as environments_api
import app.api.repositories as repositories_api
import app.api.webhooks as webhooks
import app.tasks.environment as env_tasks
from app.config import settings
from app.models import EnvironmentStatus
from app.services import repo_access
from app.services.github import InstalledRepository
from tests.test_preview_lifecycle import _env, _run, db, k8s, retry_calls  # noqa: F401 (fixtures)
from tests.test_readiness import _payload


@pytest.fixture()
def stops(monkeypatch):
    calls = []
    monkeypatch.setattr(env_tasks.destroy_environment, "delay", lambda **kw: calls.append(kw))
    return calls


# ------------------------------------------------------------------ stop

def test_stopping_queues_a_teardown_that_keeps_the_pr_open(client, auth_headers, db, user, stops):
    env = _env(db, user, EnvironmentStatus.READY)
    r = client.post(f"/api/v1/environments/{env.id}/stop", headers=auth_headers)
    assert r.status_code == 202
    assert stops == [{"environment_id": env.id, "stopped": True, "stopped_by": user.github_login}]


def test_only_a_running_or_failed_preview_can_be_stopped(client, auth_headers, db, user, stops):
    env = _env(db, user, EnvironmentStatus.DESTROYED)
    assert client.post(f"/api/v1/environments/{env.id}/stop", headers=auth_headers).status_code == 409
    assert client.post("/api/v1/environments/999/stop", headers=auth_headers).status_code == 404
    assert stops == []


def test_a_stopped_preview_is_removed_with_its_reason_and_a_comment(db, user, k8s, monkeypatch):
    env = _env(db, user, EnvironmentStatus.READY, pr=3)
    comments = []
    monkeypatch.setattr(env_tasks.github_service, "post_comment_to_pr", lambda i, r, n, body: comments.append(body))
    _run(env_tasks.destroy_environment, environment_id=env.id, stopped=True, stopped_by="octocat")
    db.refresh(env)
    assert env.status == EnvironmentStatus.DESTROYED and env.removal_reason == "stopped"
    assert env.closed_at is None
    assert "## Preview Stopped" in comments[0] and "by @octocat" in comments[0]


def test_a_push_does_not_bring_a_stopped_preview_back(db, user, monkeypatch):
    env = _env(db, user, EnvironmentStatus.DESTROYED)
    env.removal_reason = "stopped"
    db.commit()
    opened, statuses = [], []
    monkeypatch.setattr(webhooks, "handle_pull_request_opened", lambda payload: opened.append(payload))
    monkeypatch.setattr(webhooks.github_service, "update_pr_status", lambda **kw: statuses.append(kw))
    webhooks.handle_pull_request_synchronize(_payload("synchronize"))
    assert opened == []
    assert statuses[0]["description"].startswith("Not deployed: preview stopped")


def test_the_limit_message_points_to_stop(db, user, monkeypatch):
    from app.services.provisioning import PreviewLimitReached
    assert "Stop preview" in str(PreviewLimitReached(5, [1, 2, 3, 4, 5]))


# ------------------------------------------------------------------ keep available / expiry date

def _age(db, env, days):
    env.deploy_started_at = env.last_deployed_at = env.created_at = datetime.now(timezone.utc) - timedelta(days=days)
    db.commit()


def test_the_api_says_when_a_preview_will_expire(client, auth_headers, db, user):
    env = _env(db, user, EnvironmentStatus.READY)
    _age(db, env, 2)
    body = client.get(f"/api/v1/environments/{env.id}", headers=auth_headers).json()
    expires = datetime.fromisoformat(body["expires_at"])
    expected = datetime.now(timezone.utc) + timedelta(days=settings.preview_idle_days - 2)
    assert abs((expires.replace(tzinfo=expires.tzinfo or timezone.utc) - expected).total_seconds()) < 60


def test_removed_previews_and_disabled_expiry_have_no_date(client, auth_headers, db, user, monkeypatch):
    gone = _env(db, user, EnvironmentStatus.DESTROYED, pr=4)
    assert client.get(f"/api/v1/environments/{gone.id}", headers=auth_headers).json()["expires_at"] is None
    monkeypatch.setattr(settings, "preview_idle_days", 0)
    live = _env(db, user, EnvironmentStatus.READY, pr=5)
    assert client.get(f"/api/v1/environments/{live.id}", headers=auth_headers).json()["expires_at"] is None


def test_keep_available_restarts_the_idle_timer(client, auth_headers, db, user):
    env = _env(db, user, EnvironmentStatus.READY)
    _age(db, env, settings.preview_idle_days + 1)
    assert env_tasks.is_idle(env) is True
    r = client.post(f"/api/v1/environments/{env.id}/keep", headers=auth_headers)
    assert r.status_code == 200 and r.json()["kept_at"]
    db.refresh(env)
    assert env_tasks.is_idle(env) is False  # the hourly job leaves it alone now
    expires = datetime.fromisoformat(r.json()["expires_at"])
    assert expires.replace(tzinfo=expires.tzinfo or timezone.utc) > datetime.now(timezone.utc) + timedelta(days=settings.preview_idle_days - 1)


def test_a_removed_preview_cannot_be_kept(client, auth_headers, db, user):
    env = _env(db, user, EnvironmentStatus.DESTROYED)
    assert client.post(f"/api/v1/environments/{env.id}/keep", headers=auth_headers).status_code == 409


# ------------------------------------------------------------------ usage

def test_usage_counts_the_repositorys_slots(client, auth_headers, db, user, monkeypatch):
    class FakeGitHub:
        def list_installed_repositories(self):
            return [InstalledRepository("acme/app", "app", 1, False, "main", "")]

        def is_collaborator(self, *a):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    _env(db, user, EnvironmentStatus.READY, pr=3)
    _env(db, user, EnvironmentStatus.FAILED, pr=4)
    _env(db, user, EnvironmentStatus.DESTROYED, pr=5)
    body = client.get("/api/v1/repositories/acme/app/usage", headers=auth_headers).json()
    assert body == {"used": 2, "limit": settings.preview_max_active_per_repository, "pull_requests": [3, 4]}
    repo_access.clear_cache()
