"""
Dashboard sessions travel in an HttpOnly cookie rather than localStorage.

The OAuth callback sets it, the auth dependency accepts it (session tokens
only, with an X-Requested-With header on anything that is not a safe
method), and /auth/logout revokes it.
"""

import pytest

from app.api.dependencies import SESSION_COOKIE
from app.models import APIToken
from app.services.auth import GitHubOAuthService, get_github_oauth_service


class FakeOAuth:
    """Skips GitHub: exchanges any code for the fixture user."""

    def __init__(self, user):
        self.user = user

    def get_authorization_url(self, state):
        return f"https://github.com/login/oauth/authorize?state={state}"

    async def exchange_code_for_token(self, code):
        return "gho_fake"

    async def get_github_user(self, token):
        return {"id": self.user.github_id, "login": self.user.github_login, "email": None, "avatar_url": None}

    def create_or_update_user(self, db, github_user):
        return self.user

    def create_session_token(self, db, user):
        return GitHubOAuthService().create_session_token(db, user)


@pytest.fixture()
def session_cookie(db_session, user):
    raw = GitHubOAuthService().create_session_token(db_session, user)
    return {SESSION_COOKIE: raw}


def _login(client, user):
    from app.main import app

    app.dependency_overrides[get_github_oauth_service] = lambda: FakeOAuth(user)
    start = client.get("/auth/github/login", follow_redirects=False)
    state = start.cookies["ephemera_oauth_state"]
    client.cookies.set("ephemera_oauth_state", state)
    return client.get("/auth/github/callback", params={"code": "abc", "state": state}, follow_redirects=False)


def test_callback_sets_httponly_cookie_and_redirects(client, user):
    response = _login(client, user)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"

    set_cookie = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=eph_" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/" in set_cookie
    # No token anywhere a script could read it
    assert "localStorage" not in response.text
    assert "eph_" not in response.text

    # The browser is now logged in with nothing but the cookie
    assert client.get("/auth/me").json()["github_login"] == user.github_login


def test_cookie_authenticates_safe_requests(client, session_cookie):
    client.cookies.update(session_cookie)
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert client.get("/api/v1/environments/").status_code == 200


def test_cookie_mutation_needs_csrf_header(client, session_cookie):
    client.cookies.update(session_cookie)
    body = {"name": "ci"}
    denied = client.post("/api/v1/tokens/", json=body)
    assert denied.status_code == 403
    assert "X-Requested-With" in denied.json()["detail"]

    allowed = client.post("/api/v1/tokens/", json=body, headers={"X-Requested-With": "ephemera-dashboard"})
    assert allowed.status_code == 201


def test_bearer_header_never_needs_csrf_header(client, auth_headers):
    assert client.post("/api/v1/tokens/", json={"name": "ci"}, headers=auth_headers).status_code == 201


def test_api_token_in_cookie_is_rejected(client, raw_token):
    client.cookies.set(SESSION_COOKIE, raw_token)
    response = client.get("/auth/me")
    assert response.status_code == 401
    assert "dashboard sessions" in response.json()["detail"]


def test_session_cookie_cannot_export_cloud_credentials(client, session_cookie, db_session, user):
    from app.core.encryption import encrypt_credentials
    from app.models import CloudCredential, CloudProvider

    db_session.add(CloudCredential(
        user_id=user.id, provider=CloudProvider.GCP,
        credentials_encrypted=encrypt_credentials("{}"), is_active=True,
    ))
    db_session.commit()
    client.cookies.update(session_cookie)
    assert client.get("/api/v1/credentials/gcp").status_code == 403


def test_logout_revokes_session_and_clears_cookie(client, session_cookie, db_session):
    client.cookies.update(session_cookie)
    raw = session_cookie[SESSION_COOKIE]

    response = client.post("/auth/logout", headers={"X-Requested-With": "ephemera-dashboard"})
    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert f'{SESSION_COOKIE}=""' in set_cookie or f"{SESSION_COOKIE}=;" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()

    stored = db_session.query(APIToken).filter_by(token_hash=APIToken.hash_token(raw)).one()
    assert stored.is_active is False and stored.revoked_at is not None

    # Even a browser that kept the cookie is logged out
    client.cookies.set(SESSION_COOKIE, raw)
    assert client.get("/auth/me").status_code == 401


def test_logout_with_api_token_does_not_revoke_it(client, auth_headers, raw_token, db_session):
    assert client.post("/auth/logout", headers=auth_headers).status_code == 200
    stored = db_session.query(APIToken).filter_by(token_hash=APIToken.hash_token(raw_token)).one()
    assert stored.is_active is True
    assert client.get("/auth/me", headers=auth_headers).status_code == 200
