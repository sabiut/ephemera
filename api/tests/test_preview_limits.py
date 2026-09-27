"""
Previews cannot pile up: a repository holds at most
PREVIEW_MAX_ACTIVE_PER_REPOSITORY of them, and a preview with no push for
PREVIEW_IDLE_DAYS is removed and comes back on the next push.
"""

from datetime import datetime, timedelta, timezone

import pytest

import app.api.webhooks as webhooks
import app.tasks.cleanup as cleanup
import app.tasks.environment as env_tasks
from app.config import settings
from app.crud import environment as environment_crud
from app.models import Environment, EnvironmentStatus
from app.services.provisioning import EnvironmentRequest, PreviewLimitReached, request_environment
from tests.test_preview_lifecycle import _env, _run, db, k8s, queued, retry_calls  # noqa: F401 (fixtures)
from tests.test_readiness import _payload


def _request(user, pr=3):
    return EnvironmentRequest(repository_full_name="acme/app", repository_name="app", pr_number=pr, pr_title="t",
                              branch_name="b", commit_sha="c" * 40, installation_id=1, owner=user)


@pytest.fixture()
def limit(monkeypatch):
    monkeypatch.setattr(settings, "preview_max_active_per_repository", 2)
    return 2


# ------------------------------------------------------------------ limit

def test_a_repository_at_its_limit_gets_no_new_preview(db, user, limit, retry_calls):
    _env(db, user, EnvironmentStatus.READY, pr=10)
    _env(db, user, EnvironmentStatus.FAILED, pr=11)  # a failed preview still holds a namespace
    with pytest.raises(PreviewLimitReached) as e:
        request_environment(db, _request(user, pr=3))
    assert e.value.pr_numbers == [10, 11] and "#10, #11" in str(e.value)
    assert retry_calls["provision"] == []


def test_removed_previews_and_other_repositories_do_not_count(db, user, limit, retry_calls):
    _env(db, user, EnvironmentStatus.READY, pr=10)
    _env(db, user, EnvironmentStatus.DESTROYED, pr=11)
    other = _env(db, user, EnvironmentStatus.READY, pr=12)
    other.repository_full_name = "acme/other"
    db.commit()
    request_environment(db, _request(user, pr=3))
    assert len(retry_calls["provision"]) == 1


def test_zero_means_unlimited(db, user, monkeypatch, retry_calls):
    monkeypatch.setattr(settings, "preview_max_active_per_repository", 0)
    for pr in range(10, 20):
        _env(db, user, EnvironmentStatus.READY, pr=pr)
    request_environment(db, _request(user, pr=3))
    assert len(retry_calls["provision"]) == 1


def test_the_pull_request_is_told_why(db, user, limit, monkeypatch, retry_calls):
    _env(db, user, EnvironmentStatus.READY, pr=10)
    _env(db, user, EnvironmentStatus.READY, pr=11)
    comments, statuses = [], []
    monkeypatch.setattr(webhooks.github_service, "post_comment_to_pr", lambda i, r, n, body: comments.append(body))
    monkeypatch.setattr(webhooks.github_service, "update_pr_status", lambda **kw: statuses.append(kw))
    webhooks.handle_pull_request_opened(_payload("opened"))
    assert "## Preview Not Created" in comments[0] and "the limit is 2" in comments[0]
    assert statuses[0]["state"] == "error" and "limit" in statuses[0]["description"]
    assert retry_calls["provision"] == []


def test_the_api_answers_429(client, auth_headers, db_session, user, limit, monkeypatch):
    import app.api.environments as environments_module

    def over_limit(db, req):
        raise PreviewLimitReached(2, [10, 11])

    monkeypatch.setattr(environments_module, "request_environment", over_limit)
    from tests.test_environment_create import FakeGitHub
    monkeypatch.setattr(environments_module, "github_service", FakeGitHub(collaborator=True))
    r = client.post("/api/v1/environments/", json={"repository_full_name": "acme/app", "pr_number": 7}, headers=auth_headers)
    assert r.status_code == 429 and "#10, #11" in r.json()["detail"]


# ------------------------------------------------------------------ expiry

def _age(db, env, days):
    env.deploy_started_at = env.last_deployed_at = env.created_at = datetime.now(timezone.utc) - timedelta(days=days)
    db.commit()


def test_idle_previews_of_open_prs_are_expired(db, user, retry_calls):
    old = _env(db, user, EnvironmentStatus.READY, pr=10)
    _age(db, old, settings.preview_idle_days + 1)
    recent = _env(db, user, EnvironmentStatus.READY, pr=11)
    _age(db, recent, 1)
    closed = _env(db, user, EnvironmentStatus.READY, pr=12)
    _age(db, closed, 30)
    environment_crud.mark_closed(db, closed)  # the close teardown handles it
    result = _run(cleanup.expire_idle_previews)
    assert result["expired"] == [old.id]
    assert retry_calls["destroy"] == [{"environment_id": old.id, "expired": True}]


def test_expiry_can_be_turned_off(db, user, monkeypatch, retry_calls):
    monkeypatch.setattr(settings, "preview_idle_days", 0)
    _age(db, _env(db, user, EnvironmentStatus.READY, pr=10), 365)
    assert _run(cleanup.expire_idle_previews)["disabled"] is True
    assert retry_calls["destroy"] == []


def test_an_expired_preview_is_removed_and_the_pr_is_told(db, user, k8s, monkeypatch):
    env = _env(db, user, EnvironmentStatus.READY, pr=3)
    _age(db, env, settings.preview_idle_days + 1)
    comments = []
    monkeypatch.setattr(env_tasks.github_service, "post_comment_to_pr", lambda i, r, n, body: comments.append(body))
    _run(env_tasks.destroy_environment, environment_id=env.id, expired=True)
    db.refresh(env)
    assert env.status == EnvironmentStatus.DESTROYED and env.error_message.startswith("Expired")
    assert env.closed_at is None  # the PR is still open
    assert "## Preview Removed" in comments[0] and "Push a commit to bring it back" in comments[0]


def test_a_push_after_it_was_queued_keeps_the_preview(db, user, k8s):
    env = _env(db, user, EnvironmentStatus.READY, pr=3)
    _age(db, env, settings.preview_idle_days + 1)
    environment_crud.update_environment_commit(db, env, "d" * 40)  # a push lands before the teardown runs
    result = _run(env_tasks.destroy_environment, environment_id=env.id, expired=True)
    assert result["skipped"] == "no longer idle"
    assert k8s["calls"] == []


def test_a_push_brings_an_expired_preview_back(db, user, monkeypatch):
    env = _env(db, user, EnvironmentStatus.DESTROYED, error="Expired: removed after 7 days without a push")
    opened = []
    monkeypatch.setattr(webhooks, "handle_pull_request_opened", lambda payload: opened.append(payload))
    webhooks.handle_pull_request_synchronize(_payload("synchronize"))
    assert len(opened) == 1  # before: "Environment is destroyed; not updating"


def test_naive_timestamps_from_sqlite_are_compared_as_utc(db, user):
    env = _env(db, user, EnvironmentStatus.READY)
    env.deploy_started_at = env.last_deployed_at = env.created_at = datetime.utcnow() - timedelta(days=30)
    assert env_tasks.is_idle(env) is True


def test_the_landing_page_states_the_limits(client):
    page = client.get("/").text
    assert "Up to 5 previews per repository at a time." in page
    assert "no new commits for 7 days is removed; the next push brings it back." in page


# ------------------------------------------------------------------ removal reason

def test_a_slow_expiry_keeps_its_reason_through_the_cleanup_job(client, auth_headers, db, user, k8s, monkeypatch):
    # The review's reproduction: the namespace outlived the wait, the hourly
    # cleanup marked it destroyed, and "expired" was lost, so the dashboard
    # said the PR was closed and hid Recreate preview.
    env = _env(db, user, EnvironmentStatus.READY, pr=3)
    _age(db, env, settings.preview_idle_days + 1)
    k8s["outcome"], k8s["gone"] = env_tasks.kubernetes_service.DELETE_STARTED, False
    _run(env_tasks.destroy_environment, environment_id=env.id, expired=True)
    db.refresh(env)
    assert env.status == EnvironmentStatus.DESTROYING and env.removal_reason == "expired"

    env.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db.commit()
    monkeypatch.setattr(cleanup.kubernetes_service, "delete_namespace", lambda ns: cleanup.kubernetes_service.DELETE_ABSENT)
    monkeypatch.setattr(cleanup.kubernetes_service, "namespace_exists", lambda ns: False)
    _run(cleanup.cleanup_stale_environments)
    db.refresh(env)
    assert env.status == EnvironmentStatus.DESTROYED and env.removal_reason == "expired"
    body = client.get("/api/v1/environments/", headers=auth_headers).json()
    assert next(e for e in body if e["id"] == env.id)["removal_reason"] == "expired"


def test_closing_records_its_reason_and_overrides_an_expiry(db, user, k8s):
    env = _env(db, user, EnvironmentStatus.READY, pr=3)
    environment_crud.mark_closed(db, env)
    _run(env_tasks.destroy_environment, environment_id=env.id)
    db.refresh(env)
    assert env.removal_reason == "closed"

    expired = _env(db, user, EnvironmentStatus.DESTROYED, pr=4)
    expired.removal_reason = "expired"
    db.commit()
    environment_crud.mark_closed(db, expired)  # the PR closed after its preview expired
    assert expired.removal_reason == "closed"  # no longer offered for recreation


def test_recreating_clears_the_reason(db, user, retry_calls):
    env = _env(db, user, EnvironmentStatus.DESTROYED, pr=3)
    env.removal_reason = "expired"
    db.commit()
    request_environment(db, _request(user, pr=3))
    db.refresh(env)
    assert env.removal_reason is None and env.status == EnvironmentStatus.PENDING


# ------------------------------------------------------------------ admission lock

class _PgSession:
    """Just enough of a Session to see what the admission lock executes."""

    def __init__(self):
        self.executed = []

    def get_bind(self):
        from types import SimpleNamespace
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def execute(self, statement, params):
        self.executed.append((str(statement), params))


def test_admission_takes_a_per_repository_transaction_lock_on_postgres():
    from app.services.provisioning import _admission_lock
    a, b = _PgSession(), _PgSession()
    _admission_lock(a, "Acme/App")
    _admission_lock(b, "acme/app")
    assert a.executed[0][0] == "SELECT pg_advisory_xact_lock(:key)"
    assert a.executed[0][1] == b.executed[0][1]  # same repository (any case), same lock
    c = _PgSession()
    _admission_lock(c, "acme/other")
    assert c.executed[0][1] != a.executed[0][1]


def test_the_lock_is_taken_before_the_limit_is_checked(db, user, limit, monkeypatch, retry_calls):
    import app.services.provisioning as provisioning
    order = []
    monkeypatch.setattr(provisioning, "_admission_lock", lambda db, repo: order.append("lock"))
    real_check = provisioning._check_limit
    monkeypatch.setattr(provisioning, "_check_limit", lambda db, req: (order.append("check"), real_check(db, req)))
    request_environment(db, _request(user, pr=3))
    assert order == ["lock", "check"]
