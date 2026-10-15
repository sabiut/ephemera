"""
Fixes found by the product review before the managed-builds beta grows:
previews could run images from Ephemera's own registries, cancelled builds
didn't count against the month's minutes, and a repository that removed the
App kept its build slot for good.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.api.webhooks as webhooks
from app.config import settings
from app.models import Build, Environment, RepositorySettings
from app.services import managed_builds as mb
from app.services.deployment import DeploymentService, harden_deployment, image_refusal, platform_image
from tests.test_managed_builds import COMPOSE, REPO, SHA, FakeGCP, _build, on  # noqa: F401 (fixtures)
from tests.test_readiness import environment  # noqa: F401

OWN = "us-central1-docker.pkg.dev/proj/ephemera-builds-0"


@pytest.fixture()
def project(monkeypatch):
    monkeypatch.setattr(settings, "gcp_project_id", "proj")


# ------------------------------------------------------------------ images from Ephemera's registries

@pytest.mark.parametrize("image, platform", [
    ("us-central1-docker.pkg.dev/proj/ephemera-builds-3/web:abc", True),     # another repository's build
    ("us-central1-docker.pkg.dev/proj/ephemera/ephemera-api:latest", True),  # Ephemera itself
    ("europe-west1-docker.pkg.dev/proj/anything/x:1", True),                 # any region
    ("gcr.io/proj/old-image", True),
    ("US-Central1-Docker.pkg.dev/PROJ/ephemera/x", True),                    # case does not help
    ("us-central1-docker.pkg.dev/someone-else/repo/web:1", False),           # a customer's own project
    ("ghcr.io/proj/app:1", False),
    ("nginx:1.27", False),
    ("postgres", False),
])
def test_ephemeras_own_registries_are_recognised(project, image, platform):
    assert platform_image(image) is platform


def test_only_the_repositorys_own_build_slot_is_allowed(project):
    assert image_refusal(f"{OWN}/web:{SHA}", OWN) is None
    assert "Ephemera's own registry" in image_refusal("us-central1-docker.pkg.dev/proj/ephemera-builds-1/web:1", OWN)
    assert image_refusal(f"{OWN}-evil/web:1", OWN)                  # a prefix that merely starts the same
    assert image_refusal(f"{OWN}/web:1", None)                      # no slot: nothing of Ephemera's
    assert image_refusal("nginx", None) is None


def test_no_project_configured_refuses_nothing(monkeypatch):
    monkeypatch.setattr(settings, "gcp_project_id", "")
    assert image_refusal("us-central1-docker.pkg.dev/proj/ephemera/x", None) is None


def _deployment(image, init=None):
    pod = {"containers": [{"name": "web", "image": image}]}
    if init:
        pod["initContainers"] = [{"name": "setup", "image": init}]
    return {"kind": "Deployment", "metadata": {"name": "web"}, "spec": {"template": {"spec": pod}}}


def test_every_container_is_checked_including_init_containers(project):
    assert harden_deployment(_deployment("nginx")) is None
    refused = harden_deployment(_deployment("nginx", init="gcr.io/proj/tool"))
    assert refused.startswith("container setup:")
    assert harden_deployment(_deployment(f"{OWN}/web:1"), own_registry=OWN) is None


def test_the_reason_reaches_the_deploy_error(project):
    svc = DeploymentService(SimpleNamespace(enabled=True), github_service=None, base_domain="preview.test")
    applied, failed, _ = svc.apply_manifests([_deployment("us-central1-docker.pkg.dev/proj/ephemera/ephemera-api:latest")])
    assert applied == 0
    assert failed == ["Deployment/web (container web: image us-central1-docker.pkg.dev/proj/ephemera/ephemera-api:latest "
                      "is in Ephemera's own registry; previews run images from your registries, or the ones Ephemera "
                      "built for this repository)"]


def test_the_deploy_task_sets_the_repositorys_own_registry(db_session, on):
    assert mb.own_registry(db_session, "ACME/APP") is None
    row = db_session.query(RepositorySettings).one()
    row.build_slot = 0
    db_session.commit()
    assert mb.own_registry(db_session, "ACME/APP") == OWN
    svc = DeploymentService(SimpleNamespace(enabled=True), github_service=None)
    svc.set_own_registry("pr-1", OWN)
    assert svc.own_registries == {"pr-1": OWN}
    svc.set_own_registry("pr-1", None)
    assert svc.own_registries == {}


# ------------------------------------------------------------------ cancelled builds are billed

class Cancelling(FakeGCP):
    def __init__(self, statuses, after_cancel):
        super().__init__(statuses)
        self.after_cancel = after_cancel

    def get_build(self, build_id):
        if self.cancelled:
            return self.after_cancel
        return super().get_build(build_id)


def test_a_build_cancelled_by_a_newer_commit_counts_its_minutes(db_session, environment, on):
    gcp = Cancelling(["WORKING"], {"status": "CANCELLED", "startTime": "2026-10-01T10:00:00Z",
                                   "finishTime": "2026-10-01T10:07:30Z"})
    outcome = _build(db_session, environment, gcp, superseded=lambda: "d" * 40)
    assert outcome.category == "build_superseded" and outcome.duration_seconds == 450
    assert db_session.get(Build, outcome.build_id).duration_seconds == 450
    assert mb.minutes_used(db_session, REPO, now=datetime.now(timezone.utc))["minutes_used"] == 8  # 7.5, rounded up


def test_a_build_cancelled_while_queued_costs_nothing(db_session, environment, on):
    gcp = Cancelling(["QUEUED"], {"status": "CANCELLED"})
    outcome = _build(db_session, environment, gcp, superseded=lambda: "d" * 40)
    assert db_session.get(Build, outcome.build_id).duration_seconds == 0


def test_a_build_stopped_at_the_deadline_counts_its_minutes(db_session, environment, on):
    gcp = Cancelling(["WORKING"], {"status": "CANCELLED", "startTime": "2026-10-01T10:00:00Z",
                                   "finishTime": "2026-10-01T10:15:00Z"})
    outcome = _build(db_session, environment, gcp)
    assert outcome.category == "build_timeout" and db_session.get(Build, outcome.build_id).duration_seconds == 900


# ------------------------------------------------------------------ removing the App frees the slot

@pytest.fixture()
def hook(client, db_session, monkeypatch):
    async def body(request):
        return await request.body()
    monkeypatch.setattr(webhooks, "verify_github_webhook", body)
    monkeypatch.setattr(webhooks, "verify_github_delivery", lambda request: "d-1")
    monkeypatch.setattr(webhooks.repo_access, "invalidate", lambda: None)
    import app.database
    monkeypatch.setattr(app.database, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    return lambda event, payload: client.post("/webhooks/github", content=json.dumps(payload).encode(),
                                              headers={"X-GitHub-Event": event})


def _enabled(db, name, slot):
    db.add(RepositorySettings(repository_full_name=name, protect_previews=False, managed_builds_enabled=True,
                              build_plan_confirmed=[], build_slot=slot))
    db.commit()


def test_removing_repositories_turns_managed_builds_off_for_them(db_session, hook):
    _enabled(db_session, "acme/one", 0)
    _enabled(db_session, "acme/two", 1)
    hook("installation_repositories", {"action": "removed", "installation": {"id": 9},
                                       "repositories_removed": [{"full_name": "Acme/One"}]})
    rows = {r.repository_full_name: r for r in db_session.query(RepositorySettings).all()}
    assert rows["acme/one"].managed_builds_enabled is False and rows["acme/one"].build_slot == 0  # wiped, then freed
    assert rows["acme/two"].managed_builds_enabled is True


def test_uninstalling_finds_repositories_through_their_previews(db_session, hook, environment):
    environment.installation_id = 77
    db_session.commit()
    _enabled(db_session, environment.repository_full_name, 2)
    hook("installation", {"action": "deleted", "installation": {"id": 77}})   # "all repositories": none listed
    assert db_session.query(RepositorySettings).one().managed_builds_enabled is False


def test_other_installation_events_change_nothing(db_session, hook):
    _enabled(db_session, "acme/one", 0)
    hook("installation", {"action": "suspend", "installation": {"id": 9}, "repositories": [{"full_name": "acme/one"}]})
    assert db_session.query(RepositorySettings).one().managed_builds_enabled is True
