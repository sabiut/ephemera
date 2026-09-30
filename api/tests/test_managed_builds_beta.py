"""
Managed builds step 6: the beta opens to every repository while build slots
last, and an admin page shows whether previews work without help.
"""

from datetime import datetime, timedelta, timezone

import pytest

import app.api.webhooks as webhooks
from app.config import settings
from app.models import Build, Event, RepositorySettings
from app.models.environment import EnvironmentStatus
from app.services import managed_builds as mb
from app.services import metrics, repo_access, setup_check
from app.services.github import InstalledRepository
from tests.test_managed_builds import COMPOSE, REPO, SHA, FakeGCP, _build, on  # noqa: F401 (fixtures)
from tests.test_readiness import environment, wired  # noqa: F401

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ opening the beta

def test_a_star_allowlists_every_repository(monkeypatch):
    monkeypatch.setattr(settings, "managed_builds_allowlist", "*")
    assert mb.allowlisted("anyone/anything")
    monkeypatch.setattr(settings, "managed_builds_allowlist", "acme/app")
    assert mb.allowlisted("ACME/App") and not mb.allowlisted("anyone/anything")


@pytest.fixture()
def repo_page(client, monkeypatch):
    installed = InstalledRepository(full_name=REPO, name="app", installation_id=1, private=False,
                                    default_branch="main", html_url="")

    class FakeGitHub:
        def list_installed_repositories(self):
            return [installed]

        def is_collaborator(self, *a):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    monkeypatch.setattr(settings, "managed_builds_enabled", True)
    monkeypatch.setattr(settings, "managed_builds_allowlist", "*")
    monkeypatch.setattr(settings, "managed_builds_slots", 1)
    monkeypatch.setattr(setup_check, "_fetch_compose", lambda repo, ref: ("docker-compose.yml", COMPOSE))
    yield f"/api/v1/repositories/{REPO}/build-plan"
    repo_access.clear_cache()


def test_enabling_reserves_the_repositorys_place(client, auth_headers, db_session, repo_page):
    before = client.get(repo_page, headers=auth_headers).json()
    assert before["allowlisted"] and before["slots_free"] == 1 and before["has_slot"] is False
    assert client.put(repo_page, json={"enabled": True}, headers=auth_headers).status_code == 200
    assert db_session.query(RepositorySettings).one().build_slot == 0
    after = client.get(repo_page, headers=auth_headers).json()
    assert after["has_slot"] is True and after["slots_free"] == 0


def test_a_full_beta_says_so_when_enabling(client, auth_headers, db_session, repo_page):
    db_session.add(RepositorySettings(repository_full_name="other/repo", protect_previews=False, build_slot=0))
    db_session.commit()
    r = client.put(repo_page, json={"enabled": True}, headers=auth_headers)
    assert r.status_code == 409 and "beta is full" in r.json()["detail"]
    row = db_session.query(RepositorySettings).filter_by(repository_full_name=REPO).one()
    assert row.managed_builds_enabled is False and row.build_slot is None


# ------------------------------------------------------------------ what is recorded

def test_installations_are_recorded_per_repository(client, db_session, monkeypatch):
    async def body(request):
        return await request.body()
    monkeypatch.setattr(webhooks, "verify_github_webhook", body)
    monkeypatch.setattr(webhooks, "verify_github_delivery", lambda request: "d-1")
    monkeypatch.setattr(webhooks.repo_access, "invalidate", lambda: None)
    import app.database
    monkeypatch.setattr(app.database, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    import json
    for event, payload in [
        ("installation", {"action": "created", "installation": {"id": 5}, "repositories": [{"full_name": "a/one"}]}),
        ("installation_repositories", {"action": "added", "installation": {"id": 5},
                                       "repositories_added": [{"full_name": "a/two"}]}),
        ("installation", {"action": "deleted", "repositories": [{"full_name": "a/one"}]}),
    ]:
        client.post("/webhooks/github", content=json.dumps(payload).encode(), headers={"X-GitHub-Event": event})
    events = db_session.query(Event).order_by(Event.id).all()
    assert [(e.kind, e.repository_full_name, e.detail) for e in events] == [
        ("installed", "a/one", {"installation_id": 5}), ("installed", "a/two", {"installation_id": 5})]


def test_ready_and_failed_previews_are_recorded(db_session, environment):
    import app.tasks.environment as env_tasks
    environment.deploy_started_at = datetime.now(timezone.utc) - timedelta(seconds=90)
    db_session.commit()
    env_tasks._record_outcome(db_session, environment, {
        "readiness": {"web": {"verified": True}, "api": {"verified": False}}, "managed_build": {"services": ["web"]}})
    env_tasks._record_outcome(db_session, environment, None, env_tasks.DeployFailed("x", "build_timeout"))
    env_tasks._record_outcome(db_session, environment, None, RuntimeError("Services did not become ready: web (CrashLoopBackOff)"))
    ready, timeout, crash = db_session.query(Event).order_by(Event.id).all()
    assert ready.kind == "preview_ready" and ready.detail["verified"] is False and ready.detail["managed"] is True
    assert 85 <= ready.detail["seconds"] <= 120
    assert (timeout.detail["category"], crash.detail["category"]) == ("build_timeout", "crash")


def test_retries_are_recorded_without_breaking_admission(db_session, environment, monkeypatch):
    from app.crud import environment as environment_crud
    from app.services import provisioning
    from app.services.provisioning import EnvironmentRequest
    monkeypatch.setattr(provisioning.github_service, "build_environment_url", lambda *a, **k: "https://x")
    monkeypatch.setattr("app.tasks.environment.provision_environment.delay", lambda **k: None)
    environment_crud.update_environment_status(db_session, environment, EnvironmentStatus.FAILED, "boom")
    provisioning.request_environment(db_session, EnvironmentRequest(
        repository_full_name=REPO, repository_name="app", pr_number=environment.pr_number, pr_title="t",
        branch_name="b", commit_sha=SHA, installation_id=1, owner=environment.owner))
    assert [e.kind for e in db_session.query(Event).all()] == ["retry"]


def test_waiting_to_build_and_queue_time_are_recorded(db_session, environment, on):
    _running = Build(environment_id=99, repository_full_name=REPO, pr_number=1, commit_sha="0" * 40, slot=0,
                     status="building")
    db_session.add(_running)
    db_session.commit()
    assert _build(db_session, environment, FakeGCP(["SUCCESS"])).wait
    assert db_session.query(Event).one().kind == "build_wait"
    _running.status = "succeeded"
    db_session.commit()
    outcome = _build(db_session, environment, FakeGCP(["SUCCESS"], final={
        "web": "SUCCESS", "createTime": "2026-10-01T10:00:00Z", "startTime": "2026-10-01T10:00:42Z",
        "finishTime": "2026-10-01T10:01:42Z"}))
    assert db_session.get(Build, outcome.build_id).queued_seconds == 42


# ------------------------------------------------------------------ the numbers

def _ev(db, kind, repo, minutes, **detail):
    e = Event(kind=kind, repository_full_name=repo, detail=detail or None, created_at=T0 + timedelta(minutes=minutes))
    db.add(e)
    db.commit()
    return e


def test_time_to_first_verified_preview_and_who_needed_help(db_session):
    # a/fast: managed builds, Ready and verified 10 minutes after installing.
    _ev(db_session, "installed", "a/fast", 0)
    _ev(db_session, "preview_ready", "a/fast", 10, verified=True, managed=True)
    # a/slow: CI images; failed, retried, then an unverified Ready, then verified at 60.
    _ev(db_session, "installed", "a/slow", 0)
    _ev(db_session, "preview_failed", "a/slow", 5, category="image_private")
    _ev(db_session, "retry", "a/slow", 20)
    _ev(db_session, "preview_ready", "a/slow", 30, verified=False, managed=False)
    _ev(db_session, "preview_ready", "a/slow", 60, verified=True, managed=False)
    _ev(db_session, "setup_check_failed", "a/slow", 70)   # after it worked: not counted
    # a/stuck: tried and never worked. a/quiet: installed, nothing yet. old/repo: installed before events.
    _ev(db_session, "installed", "a/stuck", 0)
    _ev(db_session, "setup_check_failed", "a/stuck", 1)
    _ev(db_session, "preview_failed", "a/stuck", 2, category="build_only")
    _ev(db_session, "installed", "a/quiet", 0)
    _ev(db_session, "preview_ready", "old/repo", 3, verified=True, managed=True)
    m = metrics.compute(db_session, now=T0 + timedelta(days=1))
    assert m["installation_to_verified_ready"]["managed_builds"] == {"count": 1, "median_seconds": 600, "p90_seconds": 600}
    assert m["installation_to_verified_ready"]["ci_images"]["median_seconds"] == 3600
    assert (m["first_previews"], m["needed_help"], m["installed_no_preview_yet"]) == (3, 2, 1)
    assert m["needed_help_share"] == round(2 / 3, 3)
    assert m["needed_help_by_reason"] == {"preview_failed": 2, "retry": 1, "setup_check_failed": 1}


def test_build_numbers_and_slots(db_session, monkeypatch):
    monkeypatch.setattr(settings, "managed_builds_slots", 10)
    for status, category, seconds, queued in [("succeeded", None, 61, 5), ("succeeded", None, 120, 15),
                                              ("failed", "build_step_failed", 30, 1)]:
        db_session.add(Build(environment_id=1, repository_full_name=REPO, pr_number=1, commit_sha=SHA, slot=0,
                             status=status, failure_category=category, duration_seconds=seconds, queued_seconds=queued))
    db_session.add(RepositorySettings(repository_full_name=REPO, protect_previews=False, build_slot=0))
    db_session.commit()
    m = metrics.compute(db_session)
    b = m["builds"]
    assert b["total"] == 3 and b["outcomes"] == {"succeeded": 2, "build_step_failed": 1}
    assert b["minutes_by_repository"] == [{"repository": REPO, "minutes": 5}]   # 2 + 2 + 1
    assert b["build_time"]["median_seconds"] == 61 and b["queued_time"]["p90_seconds"] == 15
    assert m["slots"] == {"used": 1, "total": 10, "repositories": [(0, REPO)]}


def test_percentiles_use_nearest_rank():
    assert metrics._spread([]) == {"count": 0, "median_seconds": None, "p90_seconds": None}
    s = metrics._spread(range(1, 11))
    assert (s["median_seconds"], s["p90_seconds"]) == (5, 9)


def test_the_metrics_page_is_for_admins_only(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "admin_github_logins", "")
    assert client.get("/api/v1/admin/metrics", headers=auth_headers).status_code == 404
    monkeypatch.setattr(settings, "admin_github_logins", "octocat")
    body = client.get("/api/v1/admin/metrics", headers=auth_headers).json()
    assert body["window_days"] == 30 and "installation_to_verified_ready" in body
