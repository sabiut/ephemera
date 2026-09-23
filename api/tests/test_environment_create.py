"""
POST /api/v1/environments/ trusts nothing about the repository from the
caller. The installation comes from GitHub, the PR must exist, its author
becomes the owner, and the caller must be an admin, the author, or a
collaborator.
"""

import pytest

from app.api import environments as environments_module
from app.config import settings
from app.models import APIToken, EnvironmentStatus, User
from app.services.github import GitHubUnavailable, PullRequestInfo

REPO = "acme/app"
REAL_INSTALLATION = 4242
AUTHOR = dict(author_id=99, author_login="contributor", author_avatar_url="https://a/99")


class FakeGitHub:
    """Stand-in for github_service with scriptable answers."""

    def __init__(self, installation=REAL_INSTALLATION, pr="default", collaborator=False, unavailable=False):
        self.installation = installation
        self.pr = (
            PullRequestInfo(number=7, title="Add thing", state="open", head_sha="f" * 40,
                            head_ref="feature/thing", **AUTHOR)
            if pr == "default" else pr
        )
        self.collaborator = collaborator
        self.unavailable = unavailable
        self.calls = []

    def get_repo_installation_id(self, repo):
        self.calls.append(("installation", repo))
        if self.unavailable:
            raise GitHubUnavailable("not configured")
        return self.installation

    def get_pull_request(self, installation_id, repo, pr_number):
        self.calls.append(("pull", installation_id, repo, pr_number))
        return self.pr

    def is_collaborator(self, installation_id, repo, login):
        self.calls.append(("collaborator", installation_id, repo, login))
        return self.collaborator


@pytest.fixture()
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(environments_module, "github_service", fake)
    return fake


@pytest.fixture()
def captured_request(monkeypatch, db_session):
    """Replace provisioning with a recorder that still writes a real row."""
    from app.crud import environment as environment_crud

    captured = {}

    def fake_request(db, req):
        captured["req"] = req
        env = environment_crud.create_environment(
            db=db,
            repository_full_name=req.repository_full_name,
            repository_name=req.repository_name,
            pr_number=req.pr_number,
            pr_title=req.pr_title,
            branch_name=req.branch_name,
            commit_sha=req.commit_sha,
            installation_id=req.installation_id,
            owner=req.owner,
        )
        return env, "created"

    monkeypatch.setattr(environments_module, "request_environment", fake_request)
    return captured


BODY = {"repository_full_name": REPO, "pr_number": 7}


def test_pr_author_can_create_and_github_supplies_the_details(client, auth_headers, user, github, captured_request):
    # The authenticated user is the PR author.
    user.github_id = AUTHOR["author_id"]
    response = client.post("/api/v1/environments/", json=BODY, headers=auth_headers)
    assert response.status_code == 202, response.text

    req = captured_request["req"]
    assert req.installation_id == REAL_INSTALLATION
    assert req.owner.github_id == AUTHOR["author_id"]
    assert req.owner.github_login == "contributor"
    assert req.repository_name == "app"
    assert req.pr_title == "Add thing"
    assert req.branch_name == "feature/thing"
    assert req.commit_sha == "f" * 40
    # The author short-circuits the collaborator check.
    assert not any(c[0] == "collaborator" for c in github.calls)


def test_caller_supplied_owner_and_installation_are_not_trusted(client, auth_headers, github, captured_request):
    github.collaborator = True
    body = {
        **BODY,
        "installation_id": REAL_INSTALLATION,
        "user_id": 1,  # the caller, trying to become owner
        "user_login": "octocat",
        "pr_title": "Caller title",
        "commit_sha": "e" * 40,
    }
    response = client.post("/api/v1/environments/", json=body, headers=auth_headers)
    assert response.status_code == 202, response.text
    req = captured_request["req"]
    assert req.owner.github_id == AUTHOR["author_id"]  # PR author wins
    assert req.pr_title == "Caller title"  # display fields may be supplied
    assert req.commit_sha == "e" * 40


def test_wrong_installation_id_is_rejected(client, auth_headers, github, captured_request):
    github.collaborator = True
    response = client.post("/api/v1/environments/", json={**BODY, "installation_id": 1}, headers=auth_headers)
    assert response.status_code == 400
    assert "does not own" in response.json()["detail"]
    assert "req" not in captured_request


def test_app_not_installed_on_repo_is_404(client, auth_headers, github, captured_request):
    github.installation = None
    response = client.post("/api/v1/environments/", json=BODY, headers=auth_headers)
    assert response.status_code == 404
    assert "not installed" in response.json()["detail"]
    assert not any(c[0] == "pull" for c in github.calls)


def test_missing_pull_request_is_404(client, auth_headers, github, captured_request):
    github.pr = None
    response = client.post("/api/v1/environments/", json=BODY, headers=auth_headers)
    assert response.status_code == 404
    assert "Pull request #7" in response.json()["detail"]


def test_non_collaborator_is_forbidden(client, auth_headers, github, captured_request):
    github.collaborator = False
    response = client.post("/api/v1/environments/", json=BODY, headers=auth_headers)
    assert response.status_code == 403
    assert "not a collaborator" in response.json()["detail"]
    assert "req" not in captured_request
    assert ("collaborator", REAL_INSTALLATION, REPO, "octocat") in github.calls


def test_unverifiable_collaborator_check_fails_closed(client, auth_headers, github, captured_request):
    github.collaborator = None  # the App could not perform the check
    response = client.post("/api/v1/environments/", json=BODY, headers=auth_headers)
    assert response.status_code == 403


def test_collaborator_can_create(client, auth_headers, github, captured_request):
    github.collaborator = True
    response = client.post("/api/v1/environments/", json=BODY, headers=auth_headers)
    assert response.status_code == 202, response.text
    assert captured_request["req"].owner.github_login == "contributor"


def test_admin_can_create_without_being_a_collaborator(client, auth_headers, github, captured_request, monkeypatch):
    monkeypatch.setattr(settings, "admin_github_logins", "octocat")
    github.collaborator = False
    response = client.post("/api/v1/environments/", json=BODY, headers=auth_headers)
    assert response.status_code == 202, response.text
    assert not any(c[0] == "collaborator" for c in github.calls)


def test_github_not_configured_is_503(client, auth_headers, github, captured_request):
    github.unavailable = True
    response = client.post("/api/v1/environments/", json=BODY, headers=auth_headers)
    assert response.status_code == 503


def test_repository_and_pr_number_are_the_only_required_fields(client, auth_headers, github, captured_request):
    github.collaborator = True
    assert client.post("/api/v1/environments/", json={"repository_full_name": REPO}, headers=auth_headers).status_code == 422
    assert client.post("/api/v1/environments/", json=BODY, headers=auth_headers).status_code == 202


@pytest.mark.parametrize("state", ["closed"])  # GitHub reports merged PRs as closed too
def test_a_closed_pull_request_gets_no_preview(client, auth_headers, github, captured_request, state):
    # The review's reproduction: the API cleared the closed marker and queued
    # provisioning for a closed PR, bypassing the worker's guard.
    github.collaborator = True
    github.pr = PullRequestInfo(number=7, title="Add thing", state=state, head_sha="f" * 40,
                                head_ref="feature/thing", **AUTHOR)
    response = client.post("/api/v1/environments/", json=BODY, headers=auth_headers)
    assert response.status_code == 409
    assert "is closed" in response.json()["detail"]
    assert captured_request == {}  # request_environment never ran


def test_closing_marker_survives_a_rejected_api_request(client, auth_headers, github, db_session, user):
    from app.crud import environment as environment_crud

    env = environment_crud.create_environment(
        db=db_session, repository_full_name=REPO, repository_name="app", pr_number=7, pr_title="t",
        branch_name="b", commit_sha="f" * 40, installation_id=REAL_INSTALLATION, owner=user)
    environment_crud.mark_closed(db_session, env)
    github.collaborator = True
    github.pr = PullRequestInfo(number=7, title="Add thing", state="closed", head_sha="f" * 40,
                                head_ref="feature/thing", **AUTHOR)
    assert client.post("/api/v1/environments/", json=BODY, headers=auth_headers).status_code == 409
    db_session.refresh(env)
    assert env.closed_at is not None
