"""
Reviewers see teammates' previews: environments are visible to their author
and to every collaborator on the repository, as GitHub reports it.
"""

import pytest

import app.api.repositories as repositories_api
from app.config import settings
from app.models import APIToken, Environment, EnvironmentStatus, User
from app.services import repo_access
from app.services.github import GitHubUnavailable, InstalledRepository


def _repo(full_name, installation_id=1):
    return InstalledRepository(
        full_name=full_name, name=full_name.split("/")[1], installation_id=installation_id,
        private=False, default_branch="main", html_url=f"https://github.com/{full_name}",
    )


class FakeGitHub:
    def __init__(self, repos, collaborators):
        self.repos = repos
        self.collaborators = collaborators  # {full_name: {login, ...}}
        self.collaborator_calls = 0
        self.list_calls = 0
        self.unavailable = False

    def list_installed_repositories(self):
        self.list_calls += 1
        if self.unavailable:
            raise GitHubUnavailable("not configured")
        return list(self.repos)

    def is_collaborator(self, installation_id, full_name, login):
        self.collaborator_calls += 1
        return login in self.collaborators.get(full_name, set())

    def app_install_url(self):
        return "https://github.com/apps/ephemera-devs/installations/new"


@pytest.fixture(autouse=True)
def fresh_cache():
    repo_access.clear_cache()
    yield
    repo_access.clear_cache()


@pytest.fixture()
def github(monkeypatch):
    fake = FakeGitHub(
        repos=[_repo("acme/app"), _repo("acme/web"), _repo("other/secret", installation_id=2)],
        collaborators={"acme/app": {"octocat", "reviewer"}, "acme/web": {"octocat"}},
    )
    monkeypatch.setattr(repo_access, "github_service", fake)
    monkeypatch.setattr(repositories_api, "github_service", fake)
    return fake


def _user(db, github_id, login):
    u = User(github_id=github_id, github_login=login)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _headers(db, user):
    raw = APIToken.generate_token()
    db.add(APIToken(user_id=user.id, token_hash=APIToken.hash_token(raw), token_prefix=raw[:8], name="t"))
    db.commit()
    return {"Authorization": f"Bearer {raw}"}


def _env(db, owner, repo, pr):
    env = Environment(
        repository_full_name=repo, repository_name=repo.split("/")[1], pr_number=pr, pr_title="t",
        branch_name="b", commit_sha="a" * 40, installation_id=1, owner_id=owner.id, status=EnvironmentStatus.READY,
    )
    env.namespace = env.generate_namespace()
    db.add(env)
    db.commit()
    db.refresh(env)
    return env


# ------------------------------------------------------------------ service

def test_non_admin_sees_only_repositories_they_collaborate_on(github, user):
    names = [r.full_name for r in repo_access.accessible_repositories(user, admin=False)]
    assert names == ["acme/app", "acme/web"]


def test_admin_sees_every_installed_repository_without_collaborator_checks(github, user):
    names = [r.full_name for r in repo_access.accessible_repositories(user, admin=True)]
    assert names == ["acme/app", "acme/web", "other/secret"]
    assert github.collaborator_calls == 0


def test_answers_are_cached(github, user):
    repo_access.accessible_repositories(user, admin=False)
    calls = (github.list_calls, github.collaborator_calls)
    repo_access.accessible_repositories(user, admin=False)
    assert (github.list_calls, github.collaborator_calls) == calls


def test_cache_expires(github, user, monkeypatch):
    monkeypatch.setattr(settings, "repo_access_cache_seconds", 0)
    repo_access.accessible_repositories(user, admin=False)
    repo_access.accessible_repositories(user, admin=False)
    assert github.list_calls == 2


def test_unreachable_github_falls_back_to_own_prs_only(github, user):
    github.unavailable = True
    assert repo_access.accessible_repo_names(user, admin=False) == set()


# ------------------------------------------------------------------ environments API

@pytest.fixture()
def team(db_session, user, github):
    """octocat (fixture user) and reviewer collaborate on acme/app; stranger on nothing."""
    reviewer = _user(db_session, 2, "reviewer")
    stranger = _user(db_session, 3, "stranger")
    return {
        "reviewer": reviewer,
        "reviewer_headers": _headers(db_session, reviewer),
        "stranger_headers": _headers(db_session, stranger),
        "app_env": _env(db_session, user, "acme/app", 1),       # octocat's PR in a shared repo
        "web_env": _env(db_session, user, "acme/web", 2),       # octocat's PR, reviewer not a collaborator
        "own_env": _env(db_session, reviewer, "elsewhere/x", 3),  # reviewer's own PR outside the App's repos
    }


def test_reviewer_sees_teammates_previews_in_shared_repositories(client, team):
    listed = client.get("/api/v1/environments/", headers=team["reviewer_headers"]).json()
    assert {e["id"] for e in listed} == {team["app_env"].id, team["own_env"].id}
    by_id = {e["id"]: e for e in listed}
    assert by_id[team["app_env"].id]["owner_login"] == "octocat"


def test_reviewer_cannot_open_previews_outside_their_repositories(client, team):
    assert client.get(f"/api/v1/environments/{team['web_env'].id}", headers=team["reviewer_headers"]).status_code == 404
    ok = client.get(f"/api/v1/environments/namespace/{team['app_env'].namespace}", headers=team["reviewer_headers"])
    assert ok.status_code == 200


def test_non_collaborator_sees_nothing_of_the_team(client, team):
    assert client.get("/api/v1/environments/", headers=team["stranger_headers"]).json() == []


def test_filters_still_apply_inside_the_wider_scope(client, team):
    listed = client.get("/api/v1/environments/?repository=acme/app", headers=team["reviewer_headers"]).json()
    assert [e["id"] for e in listed] == [team["app_env"].id]


# ------------------------------------------------------------------ repositories API

def test_repositories_endpoint_lists_accessible_repos_and_install_link(client, auth_headers, github):
    body = client.get("/api/v1/repositories", headers=auth_headers).json()
    assert [r["full_name"] for r in body["repositories"]] == ["acme/app", "acme/web"]
    assert body["install_url"].endswith("/installations/new")


def test_repositories_endpoint_is_503_without_the_app(client, auth_headers, github):
    github.unavailable = True
    assert client.get("/api/v1/repositories", headers=auth_headers).status_code == 503


def test_repositories_endpoint_requires_auth(client):
    assert client.get("/api/v1/repositories").status_code == 401
