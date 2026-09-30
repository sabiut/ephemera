"""
Managed builds step 5: what people see. PR status follows the build, PR
comments explain build failures with the end of the log, the dashboard lists
builds and serves the whole log, and a repository can move off CI images.
"""

import pytest

import app.tasks.environment as env_tasks
from app.config import settings
from app.models import Build, RepositorySettings
from app.services import managed_builds as mb
from app.services import repo_access, setup_check
from app.services.build_plan import detect
from app.services.diagnosis import explain
from app.services.gcp import GCPError
from tests.test_managed_builds import COMPOSE, REPO, SHA, on  # noqa: F401 (fixtures)
from tests.test_readiness import environment, wired  # noqa: F401

LOG = """Step #0 - "web": Step 3/5 : RUN npm ci
Step #0 - "web": npm ERR! code E404 ```weird```
Step #1 - "worker": unrelated
Step #0 - "web": The command '/bin/sh -c npm ci' returned a non-zero code: 1"""


def _build_row(db, env, **fields):
    b = Build(environment_id=env.id, repository_full_name=REPO, pr_number=env.pr_number, commit_sha=SHA, slot=0,
              **{"status": "failed", "services": {"web": "failed", "worker": "queued"}, "log_tail": LOG,
                 "log_object": "slot-0/log-cb-1.txt", "failure_detail": "403 actAs denied on proj", **fields})
    db.add(b)
    db.commit()
    return b


# ------------------------------------------------------------------ PR status and comments

def test_the_pr_status_follows_the_build_without_flooding_github(db_session, environment, wired, on, monkeypatch):
    posted = []
    monkeypatch.setattr(env_tasks.github_service, "update_pr_status", lambda **k: posted.append(k["description"]))
    monkeypatch.setattr(env_tasks.deployment_service, "fetch_docker_compose", lambda *a: COMPOSE)

    def build(db, env, *a, stage, **k):
        for detail in ["Fetching the source of ccccccc", "Waiting for a build machine (10s)",
                       "Waiting for a build machine (20s)", "Building web (30s)", "Building web (40s)",
                       "Building worker (50s)"]:
            stage("building", detail)
        return mb.BuildOutcome(images={"web": "img"})
    monkeypatch.setattr(env_tasks.managed_builds, "build_commit", build)
    monkeypatch.setattr(env_tasks.deployment_service, "deploy_application", lambda **k: dict(wired["deploy"]))
    env_tasks._run_deployment(db_session, environment.id, 1, REPO, environment.namespace, SHA)
    assert posted == ["Fetching the source of ccccccc", "Waiting for a build machine (10s)",
                      "Building web (30s)", "Building worker (50s)"]


def test_a_failed_build_comments_with_the_failing_services_log(db_session, environment, monkeypatch):
    monkeypatch.setattr(settings, "github_oauth_redirect_uri", "https://ephemera-api.example.test/auth/github/callback")
    b = _build_row(db_session, environment)
    error = env_tasks.DeployFailed("Building web failed: npm ERR!", "build_step_failed", b.id)
    state, description, comment = env_tasks._build_notice(db_session, error, environment.id, SHA)
    assert (state, description) == ("failure", "Build failed")
    assert "~~~\nStep 3/5 : RUN npm ci" in comment and "unrelated" not in comment   # only web's lines
    assert "```weird```" in comment.split("~~~")[1]  # backticks in the log stay inside the ~~~ fence
    assert f"https://ephemera-api.example.test/dashboard#environment-{environment.id}" in comment
    assert "403 actAs" not in comment and "push a new commit" in comment


def test_a_fork_waiting_for_approval_is_pending_not_failed(db_session, environment):
    error = env_tasks.DeployFailed("This pull request comes from a fork, so building commit ccccccc needs a "
                                   "collaborator's approval", "build_fork_pending")
    state, description, comment = env_tasks._build_notice(db_session, error, environment.id, SHA)
    assert state == "pending" and "approve" in description
    assert "Approve build" in comment and "push a new commit" not in comment


def test_platform_problems_say_to_retry_not_to_fix_the_repository(db_session, environment):
    state, _, comment = env_tasks._build_notice(
        db_session, env_tasks.DeployFailed("Ephemera could not run the build", "build_platform"), environment.id, SHA)
    assert state == "failure" and "not caused by your repository" in comment


def test_ordinary_failures_keep_the_usual_comment(db_session, environment):
    assert env_tasks._build_notice(db_session, env_tasks.DeployFailed("Services did not become ready"),
                                   environment.id, SHA) is None


def test_the_failure_path_posts_the_build_notice(db_session, environment, wired, on, monkeypatch):
    from tests.test_concurrent_deploys import _run as run_task
    from contextlib import contextmanager

    @contextmanager
    def held(environment_id):
        yield env_tasks.HELD
    monkeypatch.setattr(env_tasks, "environment_lock", held)
    monkeypatch.setattr(env_tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(env_tasks.kubernetes_service, "namespace_exists", lambda ns: True)
    monkeypatch.setattr(env_tasks.kubernetes_service, "secure_namespace", lambda ns: True)
    notes = []
    monkeypatch.setattr(env_tasks, "_notify", lambda *a, **k: notes.append(a))
    monkeypatch.setattr(env_tasks.deployment_service, "fetch_docker_compose", lambda *a: COMPOSE)
    monkeypatch.setattr(env_tasks.managed_builds, "build_commit", lambda *a, **k: mb.BuildOutcome(
        error="This pull request comes from a fork, so building commit ccccccc needs approval",
        category="build_fork_pending"))
    run_task(env_tasks.update_environment, environment_id=environment.id, commit_sha=environment.commit_sha)
    assert notes[-1][4] == "pending" and "build needs approval" in notes[-1][6]
    db_session.refresh(environment)
    assert explain(environment.error_message).actions[0]["kind"] == "approve_build"


def test_build_failures_offer_the_log():
    assert explain("Building web failed: npm ERR!").actions == [{"kind": "build_log", "label": "View build log"}]


# ------------------------------------------------------------------ builds in the dashboard

@pytest.fixture()
def visible(monkeypatch):
    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "accessible_repo_names", lambda user, admin: {REPO})
    yield
    repo_access.clear_cache()


def test_builds_are_listed_without_googles_raw_errors(client, auth_headers, db_session, environment, visible):
    b = _build_row(db_session, environment, duration_seconds=75)
    body = client.get(f"/api/v1/environments/{environment.id}/builds", headers=auth_headers).json()
    assert body[0]["id"] == b.id and body[0]["has_log"] is True and body[0]["log_tail"] == LOG
    assert "failure_detail" not in body[0] and "log_object" not in body[0]


def test_the_whole_log_downloads(client, auth_headers, db_session, environment, visible, monkeypatch):
    b = _build_row(db_session, environment)

    class Logs:
        def download(self, bucket, name):
            assert name == "slot-0/log-cb-1.txt"
            return b"the whole log\n"
    monkeypatch.setattr(mb, "gcp_client", lambda: Logs())
    r = client.get(f"/api/v1/environments/{environment.id}/builds/{b.id}/log", headers=auth_headers)
    assert r.status_code == 200 and r.text == "the whole log\n"
    assert f'build-{b.id}-ccccccc.log' in r.headers["content-disposition"]

    class Gone(Logs):
        def download(self, bucket, name):
            return None
    monkeypatch.setattr(mb, "gcp_client", lambda: Gone())
    assert "30 days" in client.get(f"/api/v1/environments/{environment.id}/builds/{b.id}/log",
                                   headers=auth_headers).json()["detail"]

    class Broken(Logs):
        def download(self, bucket, name):
            raise GCPError("boom")
    monkeypatch.setattr(mb, "gcp_client", lambda: Broken())
    assert client.get(f"/api/v1/environments/{environment.id}/builds/{b.id}/log", headers=auth_headers).status_code == 503


def test_another_previews_build_log_is_not_found(client, auth_headers, db_session, environment, visible):
    b = _build_row(db_session, environment)
    b.environment_id = environment.id + 100
    db_session.commit()
    assert client.get(f"/api/v1/environments/{environment.id}/builds/{b.id}/log", headers=auth_headers).status_code == 404


# ------------------------------------------------------------------ moving off CI images

TEST_APP = """services:
  web:
    build: .
    image: ghcr.io/${EPHEMERA_REPOSITORY}:${EPHEMERA_SHA}
    ports: ["80:80"]
  echo:
    image: hashicorp/http-echo:1.0.0
"""


def test_a_repository_whose_ci_builds_its_images_can_turn_managed_builds_on(client, auth_headers, db_session, monkeypatch):
    from app.services.github import InstalledRepository
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
    monkeypatch.setattr(settings, "managed_builds_allowlist", REPO)
    monkeypatch.setattr(setup_check, "_fetch_compose", lambda repo, ref: ("docker-compose.yml", TEST_APP))
    plan = client.get(f"/api/v1/repositories/{REPO}/build-plan", headers=auth_headers).json()
    assert plan["status"] == "nothing_to_build" and plan["can_enable"] is True
    r = client.put(f"/api/v1/repositories/{REPO}/build-plan", json={"enabled": True, "signature": plan["signature"]},
                   headers=auth_headers)
    assert r.status_code == 200
    assert db_session.query(RepositorySettings).one().build_plan_confirmed == []
    assert detect("services:\n  db:\n    image: postgres\n").can_enable is False
    repo_access.clear_cache()


def test_the_dashboard_link_comes_from_the_apis_own_address(monkeypatch):
    monkeypatch.setattr(settings, "github_oauth_redirect_uri", "https://ephemera-api.devpreview.app/auth/github/callback")
    assert env_tasks._dashboard_url(5) == "https://ephemera-api.devpreview.app/dashboard#environment-5"
    monkeypatch.setattr(settings, "github_oauth_redirect_uri", "")
    assert env_tasks._dashboard_url(5) is None
