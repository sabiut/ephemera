"""
The setup check, deploy errors, diagnosis and PR notes agree with what the
deploy does now that Ephemera can build images (managed builds), and private
repositories get collaborators-only previews unless someone chooses otherwise.
"""

import pytest

from app.config import settings
from app.models import RepositorySettings
from app.services import managed_builds as mb
from app.services import preview_access, setup_check
from app.services.compose import build_only_blocker, image_options
from app.services.diagnosis import explain
from app.services.github import InstalledRepository

REPO = InstalledRepository(full_name="acme/app", name="app", installation_id=1, private=True,
                           default_branch="main", html_url="")
BUILD_ONLY = "services:\n  web:\n    build: .\n    ports: ['8080:8080']\n  db:\n    image: postgres:16\n"


def _check(managed, compose=BUILD_ONLY):
    return setup_check.check_repository(REPO, fetch=lambda repo, ref: ("docker-compose.yml", compose), managed=managed)


def _titles(report, level):
    return [c.title for c in report.checks if c.level == level]


# ------------------------------------------------------------------ the setup check

def test_with_managed_builds_on_a_build_only_service_passes(monkeypatch):
    r = _check("on")
    assert "web: built by Ephemera from each commit" in _titles(r, "ok")
    assert not _titles(r, "error") and r.ready and r.needs_image_setup is False
    web = next(s for s in r.services if s.name == "web")
    assert web.deployable and web.public and web.image == "built by Ephemera" and web.commit_image


def test_with_managed_builds_available_both_routes_are_offered():
    r = _check("available")
    err = next(c for c in r.checks if c.level == "error" and c.title.startswith("web:"))
    assert "turn on managed builds" in err.detail.lower() and "have CI push" in err.fix
    assert r.needs_image_setup is True and r.managed_builds == "available"


def test_without_managed_builds_the_ci_route_is_the_only_one():
    r = _check("unavailable")
    err = next(c for c in r.checks if c.level == "error" and c.title.startswith("web:"))
    assert "doesn't build images" in err.detail and "managed builds" not in err.fix.lower()


def test_a_build_managed_builds_cannot_do_says_why():
    r = _check("on", "services:\n  web:\n    build: {context: ., secrets: [npm]}\n    ports: ['80']\n")
    err = next(c for c in r.checks if c.title == "web: managed builds can't build it")
    assert "build secrets" in err.detail and r.needs_image_setup is True


def test_an_unpinned_image_is_replaced_by_the_build():
    r = _check("on", "services:\n  web:\n    build: .\n    image: acme/web:latest\n    ports: ['80']\n")
    ok = next(c for c in r.checks if c.title == "web: built by Ephemera from each commit")
    assert "instead of acme/web:latest" in ok.detail and r.needs_image_setup is False


def test_the_state_follows_the_platform_and_the_repository(db_session, monkeypatch):
    monkeypatch.setattr(settings, "managed_builds_enabled", False)
    assert mb.state(db_session, "acme/app") == "unavailable"
    monkeypatch.setattr(settings, "managed_builds_enabled", True)
    monkeypatch.setattr(settings, "managed_builds_allowlist", "*")
    assert mb.state(db_session, "acme/app") == "available"
    db_session.add(RepositorySettings(repository_full_name="acme/app", protect_previews=False,
                                      managed_builds_enabled=True, build_plan_confirmed=[]))
    db_session.commit()
    assert mb.state(db_session, "Acme/App") == "on"


# ------------------------------------------------------------------ one sentence everywhere

@pytest.mark.parametrize("enabled", [True, False])
def test_deploy_errors_and_diagnosis_name_the_routes_this_server_offers(monkeypatch, enabled):
    monkeypatch.setattr(settings, "managed_builds_enabled", enabled)
    blocker = build_only_blocker({"services": {"web": {"build": ".", "ports": ["80"]}}})
    assert ("managed builds" in blocker) is enabled and "push an image for each commit" in blocker
    assert "cannot build" not in blocker and "doesn't build" not in blocker
    d = explain(blocker)
    assert d.category == "build_only" and ("Turn on managed builds" in d.action) is enabled
    kinds = [a["kind"] for a in d.actions]
    assert kinds == (["managed_builds", "setup_guide"] if enabled else ["setup_guide"])
    assert image_options().endswith(".")


# ------------------------------------------------------------------ private repositories

def test_a_private_repository_defaults_to_protected_previews(db_session):
    preview_access.default_protection(db_session, "acme/secret", private=True)
    preview_access.default_protection(db_session, "acme/open", private=False)
    assert preview_access.is_protected(db_session, "acme/secret") is True
    assert preview_access.is_protected(db_session, "acme/open") is False


def test_an_explicit_choice_is_never_overwritten(db_session):
    db_session.add(RepositorySettings(repository_full_name="acme/secret", protect_previews=False))
    db_session.commit()
    preview_access.default_protection(db_session, "ACME/Secret", private=True)
    assert preview_access.is_protected(db_session, "acme/secret") is False
    assert db_session.query(RepositorySettings).count() == 1


def test_the_dashboard_shows_the_default_before_any_pull_request(client, auth_headers, monkeypatch):
    from app.services import repo_access

    class FakeGitHub:
        def list_installed_repositories(self):
            return [REPO]

        def is_collaborator(self, *a):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    body = client.get("/api/v1/repositories/acme/app/settings", headers=auth_headers).json()
    assert body["protect_previews"] is True and body["private"] is True
    repo_access.clear_cache()


def test_pull_request_webhooks_record_the_default(db_session, monkeypatch):
    import app.api.webhooks as webhooks
    from app.schemas.github import PullRequestWebhook

    monkeypatch.setattr(webhooks, "SessionLocal", lambda: _Session(db_session))
    monkeypatch.setattr(webhooks, "request_environment", lambda db, req: (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(webhooks.github_service, "get_installation_id_from_payload", lambda payload: 1)
    from tests.test_webhooks import _pr_payload
    raw = _pr_payload()
    raw["repository"].update({"full_name": "acme/secret", "name": "secret", "private": True})
    payload = PullRequestWebhook.model_validate(raw)
    try:
        webhooks.handle_pull_request_opened(payload)
    except RuntimeError:
        pass
    assert preview_access.is_protected(db_session, "acme/secret") is True


class _Session:
    """SessionLocal() used as a context manager, over the test's session."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *a):
        return False
