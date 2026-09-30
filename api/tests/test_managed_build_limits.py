"""
Managed builds step 4: limits before anyone outside the allowlist builds.
Build capacity (1 per repository, 4 platform-wide), monthly minutes, fork
approval per commit, image cleanup and wiping a slot before reuse.
"""

from datetime import datetime, timedelta, timezone

import pytest
from celery.exceptions import Retry

import app.tasks.environment as env_tasks
from app.config import settings
from app.crud import environment as environment_crud
from app.models import Build, BuildApproval, RepositorySettings
from app.models.environment import EnvironmentStatus
from app.services import managed_builds as mb
from app.services import repo_access
from app.services.diagnosis import explain
from tests.test_concurrent_deploys import _run, db, lock_log, quiet  # noqa: F401 (fixtures)
from tests.test_managed_builds import COMPOSE, REPO, SHA, FakeGCP, _build, on  # noqa: F401
from tests.test_readiness import environment, wired  # noqa: F401

NOW = datetime.now(timezone.utc)


def _row(db, repo=REPO, status="succeeded", seconds=None, created=None, env_id=1, sha=SHA, slot=0, images=None):
    b = Build(environment_id=env_id, repository_full_name=repo, pr_number=3, commit_sha=sha, slot=slot,
              status=status, duration_seconds=seconds, images=images)
    db.add(b)
    db.commit()
    if created:
        b.created_at = created
        db.commit()
    return b


# ------------------------------------------------------------------ minutes

def test_minutes_count_whole_minutes_this_month_only(db_session, on):
    _row(db_session, seconds=61)       # 2
    _row(db_session, seconds=60)       # 1
    _row(db_session, seconds=5)        # 1
    _row(db_session, seconds=600, created=NOW.replace(day=1) - timedelta(days=2))  # last month
    _row(db_session, repo="other/repo", seconds=600)
    usage = mb.minutes_used(db_session, "ACME/app")
    assert usage["minutes_used"] == 4 and usage["minutes_limit"] == 300
    assert usage["resets_at"].day == 1 and usage["resets_at"] > NOW


def test_december_resets_in_january():
    assert mb._month(datetime(2026, 12, 15, tzinfo=timezone.utc))[1] == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_a_repository_out_of_minutes_is_not_built(db_session, environment, on, monkeypatch):
    monkeypatch.setattr(settings, "managed_builds_monthly_minutes", 3)
    _row(db_session, seconds=180)
    gcp = FakeGCP(["SUCCESS"])
    outcome = _build(db_session, environment, gcp)
    assert outcome.category == "build_limit" and "used its 3 build minutes" in outcome.error
    assert gcp.created == []
    assert explain(outcome.error).category == "build_limit"


# ------------------------------------------------------------------ capacity

def test_one_build_per_repository_at_a_time(db_session, environment, on):
    _row(db_session, status="building")
    gcp = FakeGCP(["SUCCESS"])
    outcome = _build(db_session, environment, gcp)
    assert outcome.wait == "another build of this repository is running" and gcp.created == []
    assert db_session.query(Build).count() == 1  # nothing recorded for the waiting deploy


def test_a_platform_wide_cap(db_session, environment, on, monkeypatch):
    monkeypatch.setattr(settings, "managed_builds_max_running", 2)
    _row(db_session, repo="b/b", status="building")
    _row(db_session, repo="c/c", status="queued")
    assert _build(db_session, environment, FakeGCP(["SUCCESS"])).wait == "all build machines are busy"


def test_a_build_left_running_by_a_crashed_worker_stops_counting(db_session, environment, on):
    _row(db_session, status="building", created=NOW - timedelta(hours=1))
    outcome = _build(db_session, environment, FakeGCP(["SUCCESS"], final={"web": "SUCCESS"}))
    assert outcome.wait is None and outcome.error is None


def test_a_deploy_that_must_wait_is_rescheduled_not_failed(db, environment, wired, lock_log, quiet, on, monkeypatch):
    monkeypatch.setattr(env_tasks.deployment_service, "fetch_docker_compose", lambda *a: COMPOSE)
    monkeypatch.setattr(env_tasks.managed_builds, "build_commit",
                        lambda *a, **k: mb.BuildOutcome(wait="another build of this repository is running"))
    retries = []
    monkeypatch.setattr(env_tasks.update_environment, "retry", lambda **k: retries.append(k) or Retry())
    with pytest.raises(Retry):
        _run(env_tasks.update_environment, environment_id=environment.id, commit_sha=environment.commit_sha)
    assert retries and wired["waited_for"] is None
    db.refresh(environment)
    assert environment.status != EnvironmentStatus.FAILED


def test_waiting_too_long_fails_with_the_reason(db, environment, wired, lock_log, quiet, on, monkeypatch):
    monkeypatch.setattr(env_tasks.deployment_service, "fetch_docker_compose", lambda *a: COMPOSE)
    monkeypatch.setattr(env_tasks.managed_builds, "build_commit",
                        lambda *a, **k: mb.BuildOutcome(wait="all build machines are busy"))
    monkeypatch.setattr(env_tasks, "_retries_so_far", lambda t: settings.environment_lock_max_retries)
    result = _run(env_tasks.update_environment, environment_id=environment.id, commit_sha=environment.commit_sha)
    assert "waiting to build (all build machines are busy)" in result["error"]
    assert explain(result["error"]).category == "build_busy"


# ------------------------------------------------------------------ forks

def test_an_approved_fork_commit_builds_and_the_next_push_needs_approval(db_session, environment, on):
    on["head"] = "stranger/app"
    mb.approve(db_session, "ACME/APP", environment.pr_number, SHA, "maintainer")
    mb.approve(db_session, REPO, environment.pr_number, SHA, "maintainer")  # idempotent
    assert db_session.query(BuildApproval).count() == 1
    assert _build(db_session, environment, FakeGCP(["SUCCESS"], final={"web": "SUCCESS"})).error is None
    newer = mb.build_commit(db_session, environment, 1, REPO, "d" * 40, COMPOSE, stage=lambda *a: None,
                            superseded=lambda: None, gcp=FakeGCP(["SUCCESS"]), source=lambda *a: "x",
                            sleep=lambda s: None)
    assert newer.category == "build_fork_pending" and "ddddddd" in newer.error
    d = explain(newer.error, commit_sha="d" * 40)
    assert d.category == "build_fork_pending" and d.actions[0]["kind"] == "approve_build"


@pytest.fixture()
def github(monkeypatch):
    state = {"write": True}

    class FakeGitHub:
        def list_installed_repositories(self):
            return []

        def is_collaborator(self, installation_id, full_name, login):
            return True

        def can_write(self, installation_id, full_name, login):
            return state["write"]

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    monkeypatch.setattr(repo_access, "accessible_repo_names", lambda user, admin: {REPO})
    import app.api.environments as env_api
    monkeypatch.setattr(env_api, "github_service", FakeGitHub())
    yield state
    repo_access.clear_cache()


def test_only_people_who_can_push_may_approve(client, auth_headers, db_session, environment, github):
    url = f"/api/v1/environments/{environment.id}/approve-build"
    github["write"] = False
    r = client.post(url, headers=auth_headers)
    assert r.status_code == 403 and "write access" in r.json()["detail"]
    github["write"] = None
    assert client.post(url, headers=auth_headers).status_code == 503  # unverifiable: refused
    github["write"] = True
    body = client.post(url, headers=auth_headers).json()
    assert body["commit_sha"] == environment.commit_sha and body["approved_by"] == "octocat"
    assert mb.approved(db_session, REPO, environment.pr_number, environment.commit_sha)


# ------------------------------------------------------------------ cleanup

class PruneGCP:
    def __init__(self, packages=None, objects=None):
        self.packages = dict(packages or {})   # repository -> [package]
        self.objects = dict(objects or {})     # (bucket, prefix) -> [name]
        self.tags_deleted, self.packages_deleted, self.objects_deleted = [], [], []

    def delete_tag(self, repository, package, tag):
        self.tags_deleted.append((repository, package, tag))

    def list_packages(self, repository):
        return list(self.packages.get(repository, []))

    def delete_package(self, repository, package):
        self.packages_deleted.append((repository, package))
        self.packages[repository].remove(package)

    def list_objects(self, bucket, prefix):
        return list(self.objects.get((bucket, prefix), []))

    def delete_object(self, bucket, name):
        self.objects_deleted.append((bucket, name))
        self.objects[(bucket, "")].remove(name)


def test_images_no_preview_runs_are_untagged(db_session, environment, on):
    environment_crud.update_environment_status(db_session, environment, EnvironmentStatus.READY)
    current = _row(db_session, env_id=environment.id, sha=environment.commit_sha, images={"web": "i"})
    older = _row(db_session, env_id=environment.id, sha="0" * 40, images={"web": "i", "worker": "i"})
    gone = _row(db_session, env_id=999, sha="1" * 40, images={"web": "i"})     # preview record deleted
    failed = _row(db_session, env_id=environment.id, status="failed", sha="2" * 40)
    gcp = PruneGCP()
    report = mb.prune(db_session, gcp)
    assert report["tags_deleted"] == 2
    assert sorted(gcp.tags_deleted) == [("ephemera-builds-0", "web", "0" * 40), ("ephemera-builds-0", "web", "1" * 40),
                                        ("ephemera-builds-0", "worker", "0" * 40)]
    assert current.images_deleted_at is None and failed.images_deleted_at is None
    assert older.images_deleted_at and gone.images_deleted_at
    assert mb.prune(db_session, gcp)["tags_deleted"] == 0  # done once


def test_a_ready_preview_still_running_an_older_build_after_a_failed_deploy_keeps_it(db_session, environment, on):
    environment_crud.update_environment_status(db_session, environment, EnvironmentStatus.FAILED)
    older = _row(db_session, env_id=environment.id, sha="0" * 40, images={"web": "i"})
    mb.prune(db_session, PruneGCP())
    assert older.images_deleted_at is None


def test_a_slot_is_wiped_then_released_only_when_empty(db_session, on):
    row = db_session.query(RepositorySettings).one()
    row.build_slot, row.managed_builds_enabled = 0, False
    db_session.commit()
    gcp = PruneGCP(packages={"ephemera-builds-0": ["web", "worker"]},
                   objects={("proj-ephemera-build-logs-0", ""): ["log-cb-1.txt"],
                            ("proj-ephemera-build-source-0", ""): ["9-abc.tgz"]})
    first = mb.prune(db_session, gcp)
    assert first["slots_released"] == [] and row.build_slot == 0     # deleting; confirmed next run
    assert len(gcp.packages_deleted) == 2 and len(gcp.objects_deleted) == 2
    second = mb.prune(db_session, gcp)
    assert second["slots_released"] == [0] and row.build_slot is None


def test_a_slot_with_a_build_running_is_not_wiped(db_session, on):
    row = db_session.query(RepositorySettings).one()
    row.build_slot, row.managed_builds_enabled = 0, False
    db_session.commit()
    _row(db_session, status="building")
    gcp = PruneGCP(packages={"ephemera-builds-0": ["web"]})
    assert mb.prune(db_session, gcp)["slots_released"] == [] and gcp.packages_deleted == []


def test_nothing_to_prune_makes_no_google_calls(db_session, on):
    class Untouchable:
        def __getattr__(self, name):
            raise AssertionError(f"called {name}")
    assert mb.prune(db_session, Untouchable()) == {"tags_deleted": 0, "slots_released": [], "errors": 0}


def test_source_is_deleted_once_the_build_has_it(db_session, environment, on):
    gcp = FakeGCP(["SUCCESS"], final={"web": "SUCCESS"})
    outcome = _build(db_session, environment, gcp)
    assert gcp.deleted == [("proj-ephemera-build-source-0", f"{outcome.build_id}-{SHA}.tgz")]


def test_the_build_plan_shows_the_months_minutes(client, auth_headers, db_session, monkeypatch, on):
    from app.services import setup_check
    from app.services.github import InstalledRepository

    installed = InstalledRepository(full_name="acme/app", name="app", installation_id=1, private=True,
                                    default_branch="main", html_url="https://github.com/acme/app")

    class FakeGitHub:
        def list_installed_repositories(self):
            return [installed]

        def is_collaborator(self, *a):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    monkeypatch.setattr(setup_check, "_fetch_compose", lambda repo, ref: ("docker-compose.yml", COMPOSE))
    _row(db_session, seconds=125)
    usage = client.get("/api/v1/repositories/acme/app/build-plan", headers=auth_headers).json()["usage"]
    assert usage["minutes_used"] == 3 and usage["minutes_limit"] == 300
    repo_access.clear_cache()


def test_a_build_that_cannot_start_leaves_no_source_behind(db_session, environment, on):
    class Refused(FakeGCP):
        def create_build(self, body):
            raise mb.GCPError("400 invalid bucket", 400)
    gcp = Refused(["SUCCESS"])
    outcome = _build(db_session, environment, gcp)
    assert outcome.category == "build_platform"
    assert gcp.deleted == [("proj-ephemera-build-source-0", f"{outcome.build_id}-{SHA}.tgz")]
