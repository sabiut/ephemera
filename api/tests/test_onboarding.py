"""
Guided onboarding: the setup check tells a developer what a preview will do
with their compose file, and the pulls endpoint lets them create one.
"""

import pytest

import app.api.environments as environments_api
import app.api.repositories as repositories_api
from app.models import Environment, EnvironmentStatus
from app.services import repo_access, setup_check
from app.services.github import InstalledRepository, PullRequestInfo

REPO = InstalledRepository("acme/app", "app", 7, False, "main", "https://github.com/acme/app")


def _check(compose_text, filename="docker-compose.yml"):
    return setup_check.check_repository(REPO, fetch=lambda repo, ref: (filename, compose_text) if compose_text else (None, None))


def _levels(report):
    return {c.title: c.level for c in report.checks}


# ------------------------------------------------------------------ setup check

def test_missing_compose_is_an_error_with_a_fix():
    report = _check(None)
    assert report.ready is False
    [check] = report.checks
    assert check.level == "error" and check.title == "No compose file" and check.fix


def test_a_well_set_up_repository_is_ready():
    report = _check("""
services:
  web:
    build: .
    image: ghcr.io/acme/web:${EPHEMERA_SHA}
    ports: ["8080:8080"]
  db:
    image: postgres:16
    ports: ["5432"]
""")
    assert report.ready is True
    levels = _levels(report)
    assert levels["web: image tag follows each commit"] == "ok"
    web_check = next(c for c in report.checks if c.title == "web: image tag follows each commit")
    assert "checked when a preview deploys" in web_check.detail  # configuration only, no availability claim
    assert levels["Reviewers get a link"] == "ok"
    web = next(s for s in report.services if s.name == "web")
    assert web.commit_image and web.public and web.primary
    assert next(s for s in report.services if s.name == "db").primary is False


def test_build_only_service_is_an_error():
    report = _check("services:\n  web:\n    build: .\n    ports: ['8080']\n")
    assert report.ready is False
    assert _levels(report)["web: no image to deploy"] == "error"
    assert _levels(report)["Nothing for a reviewer to open"] == "error"


def test_unpinned_build_is_a_warning_not_an_error():
    report = _check("services:\n  web:\n    build: .\n    image: acme/web:latest\n    ports: ['8080']\n")
    assert report.ready is True
    assert _levels(report)["web: not built from the pull request"] == "warning"


def test_service_without_ports_is_unreachable_by_name():
    report = _check("services:\n  web:\n    image: nginx\n    ports: ['80']\n  db:\n    image: postgres:16\n")
    check = next(c for c in report.checks if c.title == "db: has no ports")
    assert check.level == "warning" and "cannot reach it by name" in check.detail


def test_ignored_compose_keys_are_explained():
    report = _check("""
services:
  web:
    image: nginx
    ports: ['80']
    volumes: ['./html:/usr/share/nginx/html']
    env_file: .env
    depends_on: [db]
    restart: always
  db:
    image: postgres
    ports: ['5432']
""")
    levels = _levels(report)
    assert levels["web: volumes is ignored"] == "warning"
    assert levels["web: env_file is ignored"] == "warning"
    assert not any("depends_on" in t or "restart" in t for t in levels)  # harmless keys stay quiet


def test_unset_and_required_variables():
    report = _check("services:\n  web:\n    image: nginx:${TAG}\n    ports: ['80']\n    environment:\n      KEY: ${API_KEY:?set API_KEY}\n")
    levels = _levels(report)
    assert levels["Variables have no value in previews"] == "warning"
    assert levels["Required variable is not set"] == "error"
    assert report.ready is False


def test_invalid_yaml_is_reported():
    report = _check("services: [unclosed\n")
    assert _levels(report)["Compose file is not valid YAML"] == "error"


# ------------------------------------------------------------------ endpoints

class FakeGitHub:
    def list_installed_repositories(self):
        return [REPO]

    def is_collaborator(self, installation_id, full_name, login):
        return login == "octocat"

    def app_install_url(self):
        return "https://github.com/apps/ephemera-devs/installations/new"

    def list_open_pulls(self, installation_id, full_name, limit=20):
        return [
            PullRequestInfo(5, "Add login", "open", "a" * 40, "login", 1, "octocat", None),
            PullRequestInfo(4, "Fix typo", "open", "b" * 40, "typo", 2, "hubot", None),
        ]


@pytest.fixture()
def github(monkeypatch):
    repo_access.clear_cache()
    fake = FakeGitHub()
    monkeypatch.setattr(repo_access, "github_service", fake)
    monkeypatch.setattr(repositories_api, "github_service", fake)
    yield fake
    repo_access.clear_cache()


def test_check_endpoint_returns_the_report(client, auth_headers, github, monkeypatch):
    monkeypatch.setattr(setup_check, "_fetch_compose", lambda repo, ref: ("compose.yml", "services:\n  web:\n    image: nginx\n    ports: ['80']\n"))
    monkeypatch.setattr(setup_check.check_repository, "__defaults__", (None, setup_check._fetch_compose))
    body = client.get("/api/v1/repositories/acme/app/check", headers=auth_headers).json()
    assert body["repository"] == "acme/app" and body["compose_file"] == "compose.yml"
    assert body["ready"] is True
    assert body["services"][0]["primary"] is True


def test_check_endpoint_is_404_for_repositories_the_caller_cannot_see(client, auth_headers, github):
    assert client.get("/api/v1/repositories/other/secret/check", headers=auth_headers).status_code == 404


def test_pulls_endpoint_joins_preview_state(client, auth_headers, github, db_session, user):
    env = Environment(
        repository_full_name="acme/app", repository_name="app", pr_number=5, pr_title="t", branch_name="login",
        commit_sha="a" * 40, installation_id=7, owner_id=user.id, status=EnvironmentStatus.READY,
        environment_url="https://pr-5-app-web.preview.test",
    )
    env.namespace = env.generate_namespace()
    db_session.add(env)
    db_session.commit()

    pulls = client.get("/api/v1/repositories/acme/app/pulls", headers=auth_headers).json()
    assert [p["number"] for p in pulls] == [5, 4]
    assert pulls[0]["environment_status"] == "ready"
    assert pulls[0]["environment_url"] == "https://pr-5-app-web.preview.test"
    assert pulls[1]["environment_status"] is None


def test_dashboard_serves_the_repositories_view(client):
    page = client.get("/dashboard").text
    assert 'id="view-repositories"' in page and "Get your first preview" in page
    assert page.count('class="notice"') >= 2  # credentials and tokens explain they are advanced


def test_check_endpoint_can_check_a_pull_requests_commit(client, auth_headers, github, monkeypatch):
    # A fix made inside the PR must be what is checked, not the default branch.
    seen = {}

    def fetch(repo, ref):
        seen["ref"] = ref
        return "docker-compose.yml", "services:\n  web:\n    image: nginx\n    ports: ['80']\n"

    monkeypatch.setattr(setup_check.check_repository, "__defaults__", (None, fetch))
    github.get_pull_request = lambda installation_id, full_name, number: PullRequestInfo(
        number, "Add login", "open", "c" * 40, "login", 1, "octocat", None)
    body = client.get("/api/v1/repositories/acme/app/check?pr=5", headers=auth_headers).json()
    assert seen["ref"] == "c" * 40
    assert body["ref_label"] == "PR #5 (ccccccc)" and body["pr_number"] == 5
    assert body["checks"][0]["detail"] == "at ccccccc"


def test_check_endpoint_is_404_for_a_missing_pull_request(client, auth_headers, github):
    github.get_pull_request = lambda *a: None
    assert client.get("/api/v1/repositories/acme/app/check?pr=99", headers=auth_headers).status_code == 404
