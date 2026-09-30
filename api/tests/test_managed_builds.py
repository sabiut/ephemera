"""
Managed builds step 3: a preview's build-only services are built from the
commit by Cloud Build, as the repository's own build slot, and deployed.
"""

import io
import tarfile
from types import SimpleNamespace

import pytest

import app.tasks.environment as env_tasks
from app.config import settings
from app.models import Build, RepositorySettings
from app.services import managed_builds as mb
from app.services.build_plan import detect
from app.services.deployment import DeploymentService
from app.services.gcp import GCPError
from tests.test_readiness import environment, wired  # noqa: F401 (fixtures)

REPO = "acme/app"
SHA = "c" * 40
COMPOSE = """
services:
  web:
    build:
      context: ./web
      dockerfile: Dockerfile.prod
      target: production
      args:
        GIT_SHA: ${EPHEMERA_SHA}
        PRICE: $$5
        FROM_ENV:
    ports: ["3000:3000"]
  worker:
    build: ./web
  db:
    image: postgres:16
"""


@pytest.fixture()
def on(db_session, monkeypatch):
    monkeypatch.setattr(settings, "managed_builds_enabled", True)
    monkeypatch.setattr(settings, "managed_builds_allowlist", "Acme/App")
    monkeypatch.setattr(settings, "gcp_project_id", "proj")
    monkeypatch.setattr(settings, "managed_builds_slots", 2)
    row = RepositorySettings(repository_full_name=REPO, protect_previews=False, managed_builds_enabled=True,
                             build_plan_confirmed=detect(COMPOSE).signature())
    db_session.add(row)
    db_session.commit()
    pulls = {"head": REPO}
    monkeypatch.setattr(mb.github_service, "get_pull_request", lambda i, r, n: SimpleNamespace(
        head_repository_full_name=pulls["head"]))
    return pulls


class FakeGCP:
    def __init__(self, statuses, final=None, log=""):
        self.statuses = list(statuses)
        self.final = final or {}
        self.log = log
        self.uploaded, self.created, self.cancelled = [], [], []

    def upload(self, bucket, name, path):
        self.uploaded.append((bucket, name))

    def create_build(self, body):
        self.created.append(body)
        return "cb-1"

    def get_build(self, build_id):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        steps = [{"id": "web", "status": "WORKING" if status == "WORKING" else self.final.get("web", "QUEUED")}]
        return {"status": status, "steps": steps, **({k: v for k, v in self.final.items() if k != "web"}
                                                     if status in mb._DONE else {})}

    def cancel_build(self, build_id):
        self.cancelled.append(build_id)

    def download(self, bucket, name):
        return self.log.encode()


def _build(db, env, gcp, superseded=lambda: None, stages=None, compose=COMPOSE):
    stages = stages if stages is not None else []
    ticks = iter(range(0, 100000, 10))
    return mb.build_commit(db, env, 1, REPO, SHA, compose, stage=lambda n, d=None: stages.append((n, d)),
                           superseded=superseded, gcp=gcp, source=lambda *a: "/tmp/src.tgz",
                           sleep=lambda s: None, clock=lambda: next(ticks))


# ------------------------------------------------------------------ the request

def test_the_build_request_builds_each_service_as_the_slot_only(monkeypatch):
    monkeypatch.setattr(settings, "gcp_project_id", "proj")
    compose = {"services": {"web": {"build": {"context": "./web", "args": {"GIT_SHA": SHA, "PRICE": "$5", "FROM_ENV": None}}}}}
    body = mb.build_request(detect(COMPOSE).buildable, compose, 3, SHA, REPO, 42, "slot-3/42.tgz")
    assert body["serviceAccount"] == "projects/proj/serviceAccounts/ephemera-build-slot-3@proj.iam.gserviceaccount.com"
    assert body["source"] == {"storageSource": {"bucket": "proj-ephemera-build-source", "object": "slot-3/42.tgz"}}
    assert body["logsBucket"] == "gs://proj-ephemera-build-logs/slot-3" and body["timeout"] == "900s"
    assert body["images"] == [f"us-central1-docker.pkg.dev/proj/ephemera-builds-3/web:{SHA}",
                              f"us-central1-docker.pkg.dev/proj/ephemera-builds-3/worker:{SHA}"]
    web = body["steps"][0]["args"]
    assert web[web.index("--file") + 1] == "web/Dockerfile.prod"   # compose: relative to the context
    assert web[web.index("--target") + 1] == "production" and web[-1] == "web"
    assert f"GIT_SHA={SHA}" in web and "PRICE=$$5" in web          # Cloud Build must not substitute it
    assert not any(a.startswith("FROM_ENV") for a in web)          # no value: nothing to pass
    assert "dev.ephemera.build=42" in web


def test_a_dockerfile_outside_the_repository_is_refused():
    plan = detect("services:\n  web:\n    build: {context: ., dockerfile: ../../etc/Dockerfile}\n")
    with pytest.raises(mb.BuildRefused):
        mb.build_request(plan.buildable, {}, 0, SHA, REPO, 1, "o")


def test_image_names_are_valid_registry_names():
    assert mb.image_name("Celery_Worker") == "celery_worker" and mb.image_name("web app!") == "web-app"


def test_githubs_top_directory_is_stripped_and_escapes_dropped(tmp_path):
    raw = tmp_path / "gh.tgz"
    with tarfile.open(raw, "w:gz") as t:
        for name, data in [("acme-app-c0ffee/Dockerfile", b"FROM nginx"), ("acme-app-c0ffee/web/app.py", b"x"),
                           ("acme-app-c0ffee/../escape", b"no"), ("acme-app-c0ffee//etc/passwd", b"no")]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        d = tarfile.TarInfo("acme-app-c0ffee/")
        d.type = tarfile.DIRTYPE
        t.addfile(d)
    out = tmp_path / "out.tgz"
    mb._strip_top_directory(str(raw), str(out), 10_000)
    with tarfile.open(out) as t:
        assert sorted(t.getnames()) == ["Dockerfile", "web/app.py"]
        assert t.extractfile("Dockerfile").read() == b"FROM nginx"


# ------------------------------------------------------------------ who builds

def test_nothing_is_built_unless_platform_allowlist_and_repository_agree(db_session, environment, on, monkeypatch):
    gcp = FakeGCP(["SUCCESS"])
    monkeypatch.setattr(settings, "managed_builds_allowlist", "someone/else")
    assert _build(db_session, environment, gcp) is None
    monkeypatch.setattr(settings, "managed_builds_allowlist", REPO)
    monkeypatch.setattr(settings, "managed_builds_enabled", False)
    assert _build(db_session, environment, gcp) is None
    monkeypatch.setattr(settings, "managed_builds_enabled", True)
    assert _build(db_session, environment, gcp, compose="services:\n  db:\n    image: postgres\n") is None
    assert gcp.created == []


def test_slots_are_assigned_once_and_run_out(db_session, on):
    rows = [db_session.query(RepositorySettings).one()]
    for name in ("b/b", "c/c"):
        rows.append(RepositorySettings(repository_full_name=name, protect_previews=False))
        db_session.add(rows[-1])
    db_session.commit()
    assert [mb.assign_slot(db_session, r) for r in rows] == [0, 1, None]
    assert mb.assign_slot(db_session, rows[0]) == 0


def test_forks_are_not_built(db_session, environment, on):
    on["head"] = "stranger/app"
    gcp = FakeGCP(["SUCCESS"])
    outcome = _build(db_session, environment, gcp)
    assert outcome.category == "build_fork_pending" and "fork" in outcome.error
    assert gcp.uploaded == [] and gcp.created == []


# ------------------------------------------------------------------ running it

def test_a_successful_build_reports_progress_and_the_images(db_session, environment, on):
    gcp = FakeGCP(["QUEUED", "WORKING", "SUCCESS"], final={
        "web": "SUCCESS", "startTime": "2026-10-01T10:00:00.123456789Z", "finishTime": "2026-10-01T10:01:20.5Z"})
    stages = []
    outcome = _build(db_session, environment, gcp, stages=stages)
    assert outcome.error is None and outcome.duration_seconds == 80
    assert outcome.images == {"web": f"us-central1-docker.pkg.dev/proj/ephemera-builds-0/web:{SHA}",
                              "worker": f"us-central1-docker.pkg.dev/proj/ephemera-builds-0/worker:{SHA}"}
    assert gcp.uploaded == [("proj-ephemera-build-source", f"slot-0/{outcome.build_id}-{SHA}.tgz")]
    details = [d for n, d in stages if n == "building"]
    assert details[0].startswith("Fetching the source") and "Waiting for a build machine" in details[1]
    assert details[2].startswith("Building web (")
    row = db_session.get(Build, outcome.build_id)
    assert (row.status, row.cloud_build_id, row.duration_seconds) == ("succeeded", "cb-1", 80)
    assert row.log_object == "slot-0/log-cb-1.txt" and row.images == outcome.images


def test_a_newer_commit_cancels_the_build(db_session, environment, on):
    gcp = FakeGCP(["WORKING"])
    outcome = _build(db_session, environment, gcp, superseded=lambda: "d" * 40)
    assert gcp.cancelled == ["cb-1"] and outcome.category == "build_superseded"
    assert db_session.get(Build, outcome.build_id).status == "cancelled"


def test_a_build_stuck_past_its_deadline_is_cancelled(db_session, environment, on):
    gcp = FakeGCP(["QUEUED"])
    outcome = _build(db_session, environment, gcp)
    assert gcp.cancelled == ["cb-1"] and outcome.category == "build_timeout"


LOG = """starting build "cb-1"
Step #0 - "web": Step 3/5 : RUN npm ci
Step #0 - "web": npm ERR! code E404
Step #0 - "web": The command '/bin/sh -c npm ci' returned a non-zero code: 1
Finished Step #0 - "web"
ERROR: build step 0 "gcr.io/cloud-builders/docker" failed: step exited with non-zero status: 1
"""


@pytest.mark.parametrize("final, log, category, words", [
    ({"web": "FAILURE"}, LOG, "build_step_failed", "returned a non-zero code: 1"),
    ({"web": "FAILURE"}, 'Step #0 - "web": unable to prepare context: path "web" not found', "build_dockerfile_missing", "isn't in this commit"),
    ({"web": "TIMEOUT"}, "", "build_timeout", "15 minutes"),
    ({"web": "SUCCESS", "failureInfo": {"type": "PUSH_FAILED"}}, "", "build_platform", "Ephemera's side"),
])
def test_failures_say_what_happened(db_session, environment, on, final, log, category, words):
    status = "TIMEOUT" if category == "build_timeout" else "FAILURE"
    outcome = _build(db_session, environment, FakeGCP([status], final=final, log=log))
    assert outcome.category == category and words in outcome.error
    row = db_session.get(Build, outcome.build_id)
    assert row.status in ("failed", "timeout") and row.log_tail == "\n".join(log.splitlines()[-60:])


def test_platform_errors_are_not_shown_to_the_pull_request(db_session, environment, on):
    class Broken(FakeGCP):
        def create_build(self, body):
            raise GCPError("POST .../builds: 403 Permission 'iam.serviceAccounts.actAs' denied on proj", 403)
    outcome = _build(db_session, environment, Broken(["SUCCESS"]))
    assert outcome.category == "build_platform" and "proj" not in outcome.error
    assert "actAs" in db_session.get(Build, outcome.build_id).failure_detail


def test_a_plan_that_changed_since_confirmation_is_built_and_noted(db_session, environment, on):
    changed = COMPOSE.replace("./web\n", "./worker\n")
    outcome = _build(db_session, environment, FakeGCP(["SUCCESS"], final={"web": "SUCCESS"}), compose=changed)
    assert outcome.error is None and "worker: context changed" in outcome.notes[0]


# ------------------------------------------------------------------ deploying what was built

def test_built_images_replace_build_sections_in_the_deploy():
    svc = DeploymentService(SimpleNamespace(enabled=True), github_service=None, base_domain="preview.test")
    captured = {}
    svc.apply_manifests = lambda manifests, revision=None: captured.setdefault("m", manifests) and (len(manifests), [], {})
    image = "us-central1-docker.pkg.dev/proj/ephemera-builds-0/web:" + SHA
    result = svc.deploy_application(1, REPO, "pr-3-app", ref=SHA, built_images={"web": image, "worker": image},
                                    compose_content=COMPOSE)
    assert result["success"] is True and sorted(result["services"]) == ["db", "web", "worker"]
    images = [c["image"] for m in captured["m"] if m["kind"] == "Deployment"
              for c in m["spec"]["template"]["spec"]["containers"]]
    assert images.count(image) == 2 and result["unpinned_builds"] == []


def test_the_deploy_task_builds_first_then_deploys_the_built_images(db_session, environment, wired, on, monkeypatch):
    calls = {}
    monkeypatch.setattr(env_tasks.deployment_service, "fetch_docker_compose", lambda *a: COMPOSE)
    monkeypatch.setattr(env_tasks.managed_builds, "build_commit", lambda db, env, *a, **k: mb.BuildOutcome(
        images={"web": "img:web"}, duration_seconds=75, build_id=1))

    def deploy(**kwargs):
        calls.update(kwargs)
        return dict(wired["deploy"])
    monkeypatch.setattr(env_tasks.deployment_service, "deploy_application", deploy)
    result = env_tasks._run_deployment(db_session, environment.id, 1, REPO, environment.namespace, SHA)
    assert calls["built_images"] == {"web": "img:web"} and calls["compose_content"] == COMPOSE
    assert result["managed_build"]["services"] == ["web"]
    assert "**Built by Ephemera** from this commit: `web` (1m 15s)" in env_tasks._deployment_summary(result)


def test_a_failed_build_stops_the_deploy_with_its_reason(db_session, environment, wired, on, monkeypatch):
    monkeypatch.setattr(env_tasks.deployment_service, "fetch_docker_compose", lambda *a: COMPOSE)
    monkeypatch.setattr(env_tasks.managed_builds, "build_commit", lambda *a, **k: mb.BuildOutcome(
        error="Building web failed: npm ERR!", category="build_step_failed"))
    monkeypatch.setattr(env_tasks.deployment_service, "deploy_application",
                        lambda **k: pytest.fail("deployed after a failed build"))
    result = env_tasks._run_deployment(db_session, environment.id, 1, REPO, environment.namespace, SHA)
    assert result["success"] is False and result["error"] == "Building web failed: npm ERR!"
    assert result["build_category"] == "build_step_failed"
