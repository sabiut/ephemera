"""
Managed builds step 2: what Ephemera would build, detected from compose and
confirmed per repository before anything builds.
"""

import pytest

from app.config import settings
from app.models import RepositorySettings
from app.services import repo_access, setup_check
from app.services.build_plan import detect, differences
from app.services.github import InstalledRepository

REPO = InstalledRepository(full_name="Acme/Shop", name="Shop", installation_id=1, private=True,
                           default_branch="main", html_url="https://github.com/Acme/Shop")

COMPOSE = """
services:
  web:
    build:
      context: ./web
      dockerfile: Dockerfile.prod
      target: production
    ports: ["3000:3000"]
  api:
    build: ./api
    image: ghcr.io/acme/api:${EPHEMERA_SHA}
    ports: ["8000:8000"]
  worker:
    build: ./api
    image: acme/worker:latest
  db:
    image: postgres:16
    ports: ["5432:5432"]
"""


def _by_name(plan):
    return {s.name: s for s in plan.services}


def test_every_service_is_classified():
    plan = detect(COMPOSE)
    assert plan.status == "ok"
    s = _by_name(plan)
    assert s["web"].kind == "build"
    assert (s["web"].context, s["web"].dockerfile, s["web"].target, s["web"].port) == ("./web", "Dockerfile.prod", "production", 3000)
    assert s["web"].public is True
    assert s["api"].kind == "ci_image"        # the repository's CI builds it per commit
    assert s["api"].image == "ghcr.io/acme/api:${EPHEMERA_SHA}"  # as written, not resolved
    assert s["worker"].kind == "build"        # an image that isn't per commit is replaced
    assert any("acme/worker:latest" in n for n in s["worker"].notes)
    assert any("No ports" in n for n in s["worker"].notes)
    assert s["worker"].dockerfile == "Dockerfile"
    assert s["db"].kind == "image" and s["db"].public is False
    assert [x["name"] for x in plan.signature()] == ["web", "worker"]


@pytest.mark.parametrize("build, reason", [
    ("{context: ., secrets: [npm_token]}", "build secrets"),
    ("{context: ., ssh: [default]}", "SSH"),
    ("{context: ., dockerfile_inline: 'FROM nginx'}", "inline Dockerfile"),
    ("{context: .., dockerfile: app/Dockerfile}", "outside the repository"),
    ("/srv/app", "outside the repository"),
    ("https://github.com/other/repo.git", "outside the repository"),
    ("{context: ., platforms: [linux/amd64, linux/arm64]}", "several platforms"),
    ("{context: ., network: host}", "network: host"),
    ("{context: ., additional_contexts: {shared: ../shared}}", "additional context"),
])
def test_unsupported_builds_say_why(build, reason):
    plan = detect(f"services:\n  web:\n    build: {build}\n    ports: ['80']\n")
    web = _by_name(plan)["web"]
    assert web.kind == "unsupported" and any(reason in r for r in web.reasons)
    assert plan.status == "nothing_to_build" and "don't support" in plan.message


def test_supported_details_are_not_flagged():
    plan = detect("services:\n  web:\n    build: {context: ./app/../web, platforms: [linux/amd64], args: {A: b}}\n")
    assert _by_name(plan)["web"].kind == "build"


def test_nothing_to_build_and_bad_compose():
    assert detect("services:\n  db:\n    image: postgres\n").status == "nothing_to_build"
    assert detect(None).status == "no_compose"
    assert detect("services: [unclosed\n").status == "invalid"
    assert detect("version: '3'\n").status == "invalid"


def test_differences_from_the_confirmed_plan():
    confirmed = detect(COMPOSE).signature()
    changed = detect(COMPOSE.replace("Dockerfile.prod", "Dockerfile").replace("worker:", "jobs:")).signature()
    diff = differences(confirmed, changed)
    assert "new service to build: jobs" in diff and "no longer built: worker" in diff
    assert any(d.startswith("web: dockerfile changed") for d in diff)
    assert differences(confirmed, confirmed) == [] and differences(None, changed) == []


# ------------------------------------------------------------------ API

@pytest.fixture()
def managed(monkeypatch):
    class FakeGitHub:
        def list_installed_repositories(self):
            return [REPO]

        def is_collaborator(self, installation_id, full_name, login):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    monkeypatch.setattr(settings, "managed_builds_enabled", True)
    monkeypatch.setattr(settings, "managed_builds_allowlist", "acme/shop")
    compose = {"text": COMPOSE}
    monkeypatch.setattr(setup_check, "_fetch_compose", lambda repo, ref: ("docker-compose.yml", compose["text"]))
    yield compose
    repo_access.clear_cache()


URL = "/api/v1/repositories/acme/shop/build-plan"


def test_hidden_until_the_platform_switches_it_on(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "managed_builds_enabled", False)
    assert client.get(URL, headers=auth_headers).status_code == 404
    assert client.get("/auth/me", headers=auth_headers).json()["features"] == {"managed_builds": False}


def test_confirm_detect_changes_and_turn_off(client, auth_headers, db_session, managed):
    assert client.get("/auth/me", headers=auth_headers).json()["features"] == {"managed_builds": True}
    plan = client.get(URL, headers=auth_headers).json()
    assert plan["status"] == "ok" and plan["managed_builds_enabled"] is False and plan["differences"] == []

    r = client.put(URL, json={"enabled": True, "signature": plan["signature"]}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["confirmed_by"] == "octocat"
    row = db_session.query(RepositorySettings).one()
    assert row.repository_full_name == "Acme/Shop" and row.managed_builds_enabled and row.protect_previews is False
    assert [s["name"] for s in row.build_plan_confirmed] == ["web", "worker"]

    managed["text"] = COMPOSE.replace("./web", "./frontend")
    after = client.get(URL, headers=auth_headers).json()
    assert after["managed_builds_enabled"] is True
    assert after["differences"] == ["web: context changed from './web' to './frontend'"]

    # Confirming what was shown before the change is refused: the plan moved.
    stale = client.put(URL, json={"enabled": True, "signature": plan["signature"]}, headers=auth_headers)
    assert stale.status_code == 409 and "changed" in stale.json()["detail"]
    client.put(URL, json={"enabled": True, "signature": after["signature"]}, headers=auth_headers)
    assert client.get(URL, headers=auth_headers).json()["differences"] == []

    off = client.put(URL, json={"enabled": False}, headers=auth_headers).json()
    assert off == {"managed_builds_enabled": False, "confirmed_by": None, "confirmed_at": None}
    db_session.refresh(row)
    assert row.build_plan_confirmed is None


def test_cannot_enable_with_nothing_to_build(client, auth_headers, managed):
    managed["text"] = "services:\n  db:\n    image: postgres\n"
    r = client.put(URL, json={"enabled": True}, headers=auth_headers)
    assert r.status_code == 409 and "Nothing for managed builds" in r.json()["detail"]


def test_other_repositories_are_not_found(client, auth_headers, managed):
    assert client.get("/api/v1/repositories/other/secret/build-plan", headers=auth_headers).status_code == 404


def test_only_allowlisted_repositories_can_turn_it_on(client, auth_headers, managed, monkeypatch):
    monkeypatch.setattr(settings, "managed_builds_allowlist", "someone/else")
    assert client.get(URL, headers=auth_headers).json()["allowlisted"] is False
    r = client.put(URL, json={"enabled": True}, headers=auth_headers)
    assert r.status_code == 403 and "limited beta" in r.json()["detail"]
    assert client.put(URL, json={"enabled": False}, headers=auth_headers).status_code == 200  # off always works
