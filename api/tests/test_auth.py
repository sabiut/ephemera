from datetime import datetime, timedelta, timezone

from app.models import APIToken


def test_me_requires_token(client):
    assert client.get("/auth/me").status_code == 401


def test_me_returns_user(client, auth_headers):
    response = client.get("/auth/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["github_login"] == "octocat"


def test_tokens_are_stored_hashed(client, auth_headers, db_session):
    response = client.post("/api/v1/tokens/", json={"name": "ci"}, headers=auth_headers)
    assert response.status_code == 201
    raw = response.json()["token"]
    assert raw.startswith("eph_")

    stored = db_session.query(APIToken).filter_by(name="ci").one()
    assert stored.token_hash != raw
    assert stored.token_hash == APIToken.hash_token(raw)

    # and the new token authenticates
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 200


def test_expired_token_is_rejected(client, db_session, user):
    token = APIToken.generate_token()
    db_session.add(
        APIToken(
            user_id=user.id,
            token_hash=APIToken.hash_token(token),
            token_prefix=token[:8],
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    db_session.commit()
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "expired" in response.json()["detail"]


def test_revoked_token_is_rejected(client, auth_headers, raw_token, db_session):
    tokens = client.get("/api/v1/tokens/", headers=auth_headers).json()
    assert len(tokens) == 1
    assert "token" not in tokens[0]  # raw value never listed

    response = client.post(f"/api/v1/tokens/{tokens[0]['id']}/revoke", headers=auth_headers)
    assert response.status_code == 200
    assert client.get("/auth/me", headers=auth_headers).status_code == 401


def test_oauth_login_sets_state_cookie_and_callback_checks_it(client):
    response = client.get("/auth/github/login", follow_redirects=False)
    assert response.status_code == 307
    assert "ephemera_oauth_state" in response.cookies
    state = response.cookies["ephemera_oauth_state"]
    assert f"state={state}" in response.headers["location"]

    # Wrong state must be rejected before any GitHub call is made
    bad = client.get("/auth/github/callback", params={"code": "abc", "state": "nope"})
    assert bad.status_code == 400


def test_environments_require_auth(client):
    assert client.get("/api/v1/environments/").status_code == 401
    assert client.post("/api/v1/environments/", json={}).status_code == 401


def test_session_tokens_cannot_export_cloud_credentials(client, db_session, user, auth_headers):
    from app.core.encryption import encrypt_credentials
    from app.models import CloudCredential, CloudProvider

    db_session.add(CloudCredential(
        user_id=user.id, provider=CloudProvider.GCP,
        credentials_encrypted=encrypt_credentials('{"type": "service_account"}'), is_active=True,
    ))
    db_session.commit()

    # A user-created API token may export
    ok = client.get("/api/v1/credentials/gcp", headers=auth_headers)
    assert ok.status_code == 200
    assert ok.json()["credentials_json"] == '{"type": "service_account"}'

    # A dashboard login session may not
    from app.services.auth import GitHubOAuthService
    session_raw = GitHubOAuthService().create_session_token(db_session, user)
    denied = client.get("/api/v1/credentials/gcp", headers={"Authorization": f"Bearer {session_raw}"})
    assert denied.status_code == 403

    # ...but it can still do ordinary dashboard things
    assert client.get("/api/v1/credentials/", headers={"Authorization": f"Bearer {session_raw}"}).status_code == 200
    listed = client.get("/api/v1/tokens/", headers=auth_headers).json()
    assert {t["token_type"] for t in listed} == {"api", "session"}
