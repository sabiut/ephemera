"""
The image setup guide generates, from a repository's own compose file, the
CI workflow and compose lines that make previews run each commit's code.
"""

import yaml

import app.api.repositories as repositories_api
from app.services import repo_access, setup_check
from app.services.github import InstalledRepository
from app.services.setup_guide import build_guide

REPO = InstalledRepository(full_name="Acme/Shop_App", name="Shop_App", installation_id=1, private=False,
                           default_branch="main", html_url="https://github.com/Acme/Shop_App")


def _steps(workflow):
    return yaml.safe_load(workflow)["jobs"]["images"]["steps"]


def test_one_build_only_service_gets_a_workflow_and_compose_line():
    g = build_guide(REPO, "services:\n  web:\n    build: .\n    ports: ['8000:8000']\n  db:\n    image: postgres:16\n")
    assert g.status == "needs_setup" and [s.name for s in g.services] == ["web"]
    assert g.services[0].image == "ghcr.io/acme/shop_app"  # lowercase, one service keeps the repo name
    wf = yaml.safe_load(g.workflow)
    assert wf[True]["push"]["branches"] == ["main"]  # YAML reads "on" as True
    assert wf["permissions"] == {"contents": "read", "packages": "write"}
    # Tagged with the PR's head commit, which is what ${EPHEMERA_SHA} becomes.
    assert wf["jobs"]["images"]["env"]["SHA"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    build = _steps(g.workflow)[-1]
    assert build["with"] == {"context": ".", "push": True, "tags": "ghcr.io/acme/shop_app:${{ env.SHA }}"}
    snippet = yaml.safe_load(g.compose_snippet)["services"]["web"]
    assert snippet["image"] == "ghcr.io/acme/shop_app:${EPHEMERA_SHA}"


def test_several_services_get_one_image_each_with_their_own_context_and_dockerfile():
    compose = """
services:
  api:
    build: ./api
    ports: ["8000:8000"]
  celery_worker:
    build:
      context: ./api
      dockerfile: docker/Dockerfile.worker
  web:
    build: ./web
    image: acme/web:latest
  redis:
    image: redis:7
"""
    g = build_guide(REPO, compose)
    assert [s.name for s in g.services] == ["api", "celery_worker", "web"]
    builds = {s["name"]: s["with"] for s in _steps(g.workflow) if s.get("name", "").startswith("Build and push")}
    assert builds["Build and push api"]["tags"] == "ghcr.io/acme/shop_app-api:${{ env.SHA }}"
    # compose resolves dockerfile against the context; the action against the repo root
    assert builds["Build and push celery_worker"]["file"] == "api/docker/Dockerfile.worker"
    assert builds["Build and push celery_worker"]["tags"] == "ghcr.io/acme/shop_app-celery_worker:${{ env.SHA }}"
    snippet = yaml.safe_load(g.compose_snippet)["services"]
    assert snippet["web"]["image"] == "ghcr.io/acme/shop_app-web:${EPHEMERA_SHA}"
    assert "# was acme/web:latest" in g.compose_snippet  # an unpinned image is replaced, and says so
    assert snippet["celery_worker"]["build"] == {"context": "./api", "dockerfile": "docker/Dockerfile.worker"}


def test_already_pinned_services_need_nothing():
    g = build_guide(REPO, "services:\n  web:\n    build: .\n    image: ghcr.io/acme/web:${EPHEMERA_SHA}\n")
    assert g.status == "nothing_to_do" and not g.workflow


def test_missing_or_broken_compose_is_explained():
    assert build_guide(REPO, None).status == "no_compose"
    assert build_guide(REPO, "services: [unclosed\n").status == "invalid"


def test_registry_access_and_caveats_are_spelled_out():
    g = build_guide(REPO, "services:\n  web:\n    build: .\n")
    assert any("Change visibility" in s and "Public" in s for s in g.registry_steps)
    assert any("forks" in n for n in g.notes)
    assert not any("private" in n.lower() for n in g.notes)
    private = InstalledRepository(**{**REPO.__dict__, "private": True})
    assert any("This repository is private" in n for n in build_guide(private, "services:\n  web:\n    build: .\n").notes)


def test_the_setup_check_says_when_the_guide_applies():
    fetch = lambda repo, ref: ("docker-compose.yml", "services:\n  web:\n    build: .\n    ports: ['80']\n")
    assert setup_check.check_repository(REPO, fetch=fetch).needs_image_setup is True
    pinned = lambda repo, ref: ("docker-compose.yml", "services:\n  web:\n    image: nginx\n    ports: ['80']\n")
    assert setup_check.check_repository(REPO, fetch=pinned).needs_image_setup is False


def test_setup_guide_endpoint(client, auth_headers, monkeypatch):
    class FakeGitHub:
        def list_installed_repositories(self):
            return [REPO]

        def is_collaborator(self, installation_id, full_name, login):
            return True

    repo_access.clear_cache()
    monkeypatch.setattr(repo_access, "github_service", FakeGitHub())
    monkeypatch.setattr(setup_check, "_fetch_compose", lambda repo, ref: ("docker-compose.yml", "services:\n  web:\n    build: .\n"))
    body = client.get("/api/v1/repositories/Acme/Shop_App/setup-guide", headers=auth_headers).json()
    assert body["status"] == "needs_setup" and body["workflow_path"] == ".github/workflows/ephemera-images.yml"
    assert "docker/build-push-action" in body["workflow"]
    assert client.get("/api/v1/repositories/other/secret/setup-guide", headers=auth_headers).status_code == 404
    repo_access.clear_cache()
