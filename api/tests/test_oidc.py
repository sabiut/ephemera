"""
CI integration step 6 (docs/ci-pipeline.md): GitHub Actions jobs deploy
previews with their OIDC identity instead of a stored token. Tokens here are
really RS256-signed, with a test key standing in for GitHub's.
"""

import json
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings
from app.models import APIToken, RepositorySettings, User
from app.services import oidc, repo_access
from app.services.github import InstalledRepository
from tests.test_pipeline_previews import BODY, queued  # noqa: F401 (fixture)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)
INSTALLED = InstalledRepository(full_name="Acme/Shop", name="Shop", installation_id=5, private=False,
                                default_branch="main", html_url="")


def identity(key=KEY, **claims):
    now = int(time.time())
    payload = {"iss": "https://token.actions.githubusercontent.com", "aud": "ephemera", "iat": now,
               "exp": now + 300, "repository": "acme/shop", "actor": "octobot", "actor_id": 4242,
               "run_id": "99", "workflow_ref": "acme/shop/.github/workflows/preview.yml@refs/pull/7/merge", **claims}
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, key, algorithm="RS256")


@pytest.fixture()
def github(monkeypatch, db_session):
    monkeypatch.setattr(oidc, "_signing_key", lambda token: KEY.public_key())
    monkeypatch.setattr(oidc, "_installed", lambda name: INSTALLED if name.lower() == "acme/shop" else None)

    class NotACollaborator:
        def list_installed_repositories(self):
            return [INSTALLED]

        def is_collaborator(self, *a):
            return False

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", NotACollaborator())
    yield
    repo_access.clear_cache()


@pytest.fixture()
def allowed(db_session):
    db_session.add(RepositorySettings(repository_full_name="Acme/Shop", protect_previews=False, github_actions_oidc=True))
    db_session.commit()


# ------------------------------------------------------------------ verifying GitHub's token

def test_a_genuine_token_is_verified(github):
    assert oidc.verify(identity())["repository"] == "acme/shop"


@pytest.mark.parametrize("token, words", [
    (lambda: identity(aud="someone-else"), "audience 'ephemera'"),
    (lambda: identity(iss="https://gitlab.com"), "not issued by GitHub Actions"),
    (lambda: identity(exp=int(time.time()) - 120), "expired"),
    (lambda: identity(repository=None), "not valid"),
    (lambda: identity(actor_id=None), "not valid"),
    (lambda: identity(key=OTHER), "not valid"),            # signed by someone else's key
    (lambda: "not-a-jwt", "isn't an identity token"),
])
def test_anything_else_is_refused(github, token, words):
    with pytest.raises(oidc.OIDCError, match=words):
        oidc.verify(token())


# ------------------------------------------------------------------ the exchange

def test_the_repository_must_allow_it_and_have_the_app(db_session, github):
    with pytest.raises(oidc.OIDCError, match="doesn't let GitHub Actions") as e:
        oidc.exchange(db_session, identity())
    assert e.value.status == 403
    with pytest.raises(oidc.OIDCError, match="isn't installed") as e:
        oidc.exchange(db_session, identity(repository="acme/other"))
    assert e.value.status == 404


def test_an_allowed_repository_gets_a_short_lived_pipeline_token(db_session, github, allowed):
    got = oidc.exchange(db_session, identity())
    assert got.repository == "Acme/Shop" and got.actor == "octobot"
    token = db_session.query(APIToken).filter_by(token_hash=APIToken.hash_token(got.token)).one()
    assert (token.token_type, token.scope, token.repository_full_name) == ("oidc", "previews", "Acme/Shop")
    expires = token.expires_at if token.expires_at.tzinfo else token.expires_at.replace(tzinfo=timezone.utc)
    assert timedelta(minutes=59) < expires - datetime.now(timezone.utc) <= timedelta(minutes=60)
    assert db_session.get(User, token.user_id).github_login == "octobot"     # the workflow's actor owns it


def test_the_exchange_endpoint(client, github, allowed):
    r = client.post("/api/v1/oidc/token", json={"token": identity()})
    assert r.status_code == 200 and r.json()["scope"] == "previews" and r.json()["expires_in"] == 3600
    assert client.post("/api/v1/oidc/token", json={"token": identity(aud="x")}).status_code == 401


# ------------------------------------------------------------------ using it

def _exchanged(client):
    return {"Authorization": f"Bearer {client.post('/api/v1/oidc/token', json={'token': identity()}).json()['token']}"}


def test_the_token_deploys_without_a_collaborator_and_nothing_else(client, db_session, github, allowed, queued):
    headers = _exchanged(client)
    r = client.post("/api/v1/previews", json=BODY, headers=headers)
    assert r.status_code == 202, r.text                       # octobot is no collaborator: trust is the claim
    assert client.post("/api/v1/previews", json={**BODY, "repository": "acme/other"}, headers=headers).status_code == 403
    assert client.get("/api/v1/environments/", headers=headers).status_code == 403
    assert client.get("/api/v1/repositories", headers=headers).status_code == 403


def test_another_runs_token_can_remove_what_this_run_left(client, db_session, github, allowed, queued):
    created = client.post("/api/v1/previews", json=BODY, headers=_exchanged(client)).json()
    later = {"Authorization": "Bearer " + client.post("/api/v1/oidc/token", json={
        "token": identity(actor="someone", actor_id=777)}).json()["token"]}
    assert client.get(f"/api/v1/previews/{created['id']}", headers=later).status_code == 200
    assert client.delete(f"/api/v1/previews/{created['id']}", headers=later).status_code == 202


def test_turning_it_off_stops_existing_tokens(client, db_session, github, allowed, queued):
    headers = _exchanged(client)
    db_session.query(RepositorySettings).update({"github_actions_oidc": False})
    db_session.commit()
    r = client.post("/api/v1/previews", json=BODY, headers=headers)
    assert r.status_code == 403 and "no longer lets GitHub Actions" in r.json()["detail"]


def test_collaborators_switch_it_on_the_repository_page(client, auth_headers, db_session, monkeypatch):
    class Collaborator:
        def list_installed_repositories(self):
            return [INSTALLED]

        def is_collaborator(self, *a):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", Collaborator())
    assert client.get("/api/v1/repositories/acme/shop/pipelines", headers=auth_headers).json() == {
        "github_actions_oidc": False, "audience": settings.oidc_audience, "updated_by_login": None}
    r = client.put("/api/v1/repositories/acme/shop/pipelines", json={"github_actions_oidc": True}, headers=auth_headers)
    assert r.json()["github_actions_oidc"] is True and r.json()["updated_by_login"] == "octocat"
    assert oidc.allowed(db_session, "acme/shop")
    repo_access.clear_cache()


# ------------------------------------------------------------------ the CLI, end to end

def test_the_cli_in_github_actions_needs_no_stored_token(client, db_session, github, allowed, queued, monkeypatch,
                                                         capsys):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "cli"))
    from ephemera_cli import cli

    for var in ("EPHEMERA_TOKEN", "GITHUB_SHA", "GITHUB_REPOSITORY", "GITHUB_EVENT_PATH", "EPHEMERA_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://pipelines.actions.test/idtoken?api-version=2.0")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-secret")

    def send(method, url, headers, data):
        if url.startswith("https://pipelines.actions.test/"):      # GitHub's token service
            assert "audience=ephemera" in url and headers["Authorization"] == "Bearer runner-secret"
            return 200, json.dumps({"value": identity()}).encode()
        r = client.request(method, "/" + url.split("://", 1)[1].split("/", 1)[1], headers=headers, content=data)
        return r.status_code, r.content

    code = cli.main(["preview", "deploy", "--repo", "acme/shop", "--name", "pr-7", "--commit", "a" * 40,
                     "--image", f"web={BODY['images']['web']}", "--output", "json"], send=send)
    out, err = capsys.readouterr()
    assert code == 0, err
    assert json.loads(out)["action"] == "created" and "using GitHub Actions OIDC" in err
