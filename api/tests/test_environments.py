"""
Environment reads are scoped to the caller: a user sees environments for PRs
they authored, admins (ADMIN_GITHUB_LOGINS) see everything, and other
people's environments are a 404 rather than a 403.
"""

import pytest

from app.config import settings
from app.models import APIToken, Environment, EnvironmentStatus, User


def _token_for(db_session, user):
    raw = APIToken.generate_token()
    db_session.add(
        APIToken(
            user_id=user.id,
            token_hash=APIToken.hash_token(raw),
            token_prefix=raw[:8],
            name="test",
        )
    )
    db_session.commit()
    return {"Authorization": f"Bearer {raw}"}


def _environment(db_session, owner, pr_number, repo="acme/app", status=EnvironmentStatus.READY):
    env = Environment(
        repository_full_name=repo,
        repository_name=repo.split("/")[1],
        pr_number=pr_number,
        pr_title=f"PR {pr_number}",
        branch_name=f"feature-{pr_number}",
        commit_sha="a" * 40,
        installation_id=1,
        owner_id=owner.id,
        status=status,
    )
    env.namespace = env.generate_namespace()
    db_session.add(env)
    db_session.commit()
    db_session.refresh(env)
    return env


@pytest.fixture()
def other_user(db_session):
    u = User(github_id=2, github_login="hubot")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def other_headers(db_session, other_user):
    return _token_for(db_session, other_user)


@pytest.fixture()
def admin_headers(db_session, monkeypatch):
    admin = User(github_id=3, github_login="Ops-Admin")
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    # Login matching is case-insensitive, like GitHub itself.
    monkeypatch.setattr(settings, "admin_github_logins", "someone-else, ops-admin")
    return _token_for(db_session, admin)


@pytest.fixture()
def environments(db_session, user, other_user):
    return {
        "mine": _environment(db_session, user, 1),
        "mine_destroyed": _environment(db_session, user, 2, status=EnvironmentStatus.DESTROYED),
        "mine_other_repo": _environment(db_session, user, 3, repo="acme/web"),
        "theirs": _environment(db_session, other_user, 4),
    }


def test_listing_requires_token(client):
    assert client.get("/api/v1/environments/").status_code == 401


def test_user_sees_only_their_own_environments(client, auth_headers, environments):
    response = client.get("/api/v1/environments/", headers=auth_headers)
    assert response.status_code == 200
    ids = {e["id"] for e in response.json()}
    assert ids == {environments["mine"].id, environments["mine_destroyed"].id, environments["mine_other_repo"].id}
    assert environments["theirs"].id not in ids


def test_other_user_sees_only_theirs(client, other_headers, environments):
    response = client.get("/api/v1/environments/", headers=other_headers)
    assert [e["id"] for e in response.json()] == [environments["theirs"].id]


def test_admin_sees_everything(client, admin_headers, environments):
    response = client.get("/api/v1/environments/", headers=admin_headers)
    assert response.status_code == 200
    assert {e["id"] for e in response.json()} == {e.id for e in environments.values()}


def test_admin_flag_is_reported_on_me(client, auth_headers, admin_headers):
    assert client.get("/auth/me", headers=auth_headers).json()["is_admin"] is False
    assert client.get("/auth/me", headers=admin_headers).json()["is_admin"] is True


def test_filters_stay_inside_the_callers_scope(client, auth_headers, environments):
    active = client.get("/api/v1/environments/?active_only=true", headers=auth_headers).json()
    assert {e["id"] for e in active} == {environments["mine"].id, environments["mine_other_repo"].id}

    by_repo = client.get("/api/v1/environments/?repository=acme/app", headers=auth_headers).json()
    assert {e["id"] for e in by_repo} == {environments["mine"].id, environments["mine_destroyed"].id}

    # The other user's environment is in acme/app too, but the filter cannot reach it.
    assert environments["theirs"].id not in {e["id"] for e in by_repo}


def test_listing_is_newest_first_and_limited(client, auth_headers, environments):
    response = client.get("/api/v1/environments/?limit=2", headers=auth_headers)
    listed = response.json()
    assert len(listed) == 2
    assert listed[0]["pr_number"] > listed[1]["pr_number"]


def test_other_users_environment_is_404_by_id_and_namespace(client, auth_headers, admin_headers, environments):
    theirs = environments["theirs"]
    assert client.get(f"/api/v1/environments/{theirs.id}", headers=auth_headers).status_code == 404
    assert client.get(f"/api/v1/environments/namespace/{theirs.namespace}", headers=auth_headers).status_code == 404

    # ...but visible to its owner and to an admin.
    assert client.get(f"/api/v1/environments/{theirs.id}", headers=admin_headers).status_code == 200
    mine = environments["mine"]
    assert client.get(f"/api/v1/environments/{mine.id}", headers=auth_headers).json()["id"] == mine.id
    assert client.get(f"/api/v1/environments/namespace/{mine.namespace}", headers=auth_headers).json()["id"] == mine.id
