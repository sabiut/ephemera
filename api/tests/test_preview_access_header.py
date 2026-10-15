"""
CI integration step 4 (docs/ci-pipeline.md): a pipeline's tests reach a
protected preview with a short-lived X-Ephemera-Access header instead of a
browser sign-in, and only for as long as its owner may view the preview.
"""

import time

import app.core.signing as signing
from app.models import Environment
from app.services import preview_access
from tests.test_pipeline_previews import BODY, github, queued  # noqa: F401 (fixtures)
from tests.test_protected_previews import HOST, NS, env, no_repo_access, protected, stranger  # noqa: F401


def _check(client, header=None, host=HOST):
    headers = {"X-Original-URL": f"https://{host}/api/orders"}
    if header:
        headers[preview_access.ACCESS_HEADER] = header
    client.cookies.clear()
    return client.get("/preview-auth/check", headers=headers).status_code


def test_the_header_lets_the_owners_tests_in(client, env, protected, user):
    assert _check(client) == 401                                       # protected: no header, no entry
    assert _check(client, preview_access.mint_access(NS, user.id)) == 200


def test_it_is_for_one_preview_and_one_purpose(client, env, protected, user):
    assert _check(client, preview_access.mint_access("pr-9-other-123456", user.id)) == 401
    assert _check(client, preview_access.mint_cookie(NS, user.id)) == 401    # a cookie value is not a header
    assert _check(client, "forged.value") == 401


def test_it_follows_the_owners_access_and_expires(client, env, protected, stranger, monkeypatch):
    assert _check(client, preview_access.mint_access(NS, stranger.id)) == 401   # can't view: header useless
    value = preview_access.mint_access(NS, env.owner_id, 15 * 60)
    real = time.time
    monkeypatch.setattr(signing.time, "time", lambda: real() + 16 * 60)
    assert _check(client, value) == 401


def test_lifetimes_are_bounded():
    claims = preview_access.read_access(preview_access.mint_access(NS, 1, 10 ** 6))
    assert claims["exp"] - time.time() <= preview_access.ACCESS_MAX_TTL + 1
    claims = preview_access.read_access(preview_access.mint_access(NS, 1, 1))
    assert claims["exp"] - time.time() >= 59


def test_the_api_issues_it_for_a_pipeline_preview(client, db_session, user, github, queued, auth_headers):
    created = client.post("/api/v1/previews", json=BODY, headers=auth_headers).json()
    r = client.post(f"/api/v1/previews/{created['id']}/access", json={"minutes": 30}, headers=auth_headers)
    body = r.json()
    assert r.status_code == 200 and body["header"] == "X-Ephemera-Access" and body["expires_in"] == 1800
    namespace = db_session.get(Environment, created["id"]).namespace
    assert preview_access.read_access(body["value"])["ns"] == namespace and body["protected"] is False
    assert client.post(f"/api/v1/previews/{created['id']}/access", json={"minutes": 61},
                       headers=auth_headers).status_code == 422
    assert client.post("/api/v1/previews/99999/access", headers=auth_headers).status_code == 404
