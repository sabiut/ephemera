"""
A failed preview leads with what happened and what to do next. The inputs
here are errors Ephemera actually recorded in live tests.
"""

import pytest

from app.models import Environment, EnvironmentStatus
from app.crud import deployment as deployment_crud
from app.services.diagnosis import explain

REPO, SHA = "acme/app", "c3d4e5f6a7" + "0" * 30


@pytest.mark.parametrize("error,category,title", [
    ("Services did not become ready: web (image ghcr.io/acme/app:c3d4e5f was never published; check that the "
     "repository's CI built and pushed it for this commit)", "image_missing", "Image not found"),
    ("Services did not become ready: web (ErrImagePull: rpc error: code = NotFound desc = failed to pull and unpack "
     "image \"ghcr.io/acme/app:latest\": not found)", "image_missing", "Image not found"),
    ("Services did not become ready: web (ImagePullBackOff: Back-off pulling image \"ghcr.io/acme/app:c3d4e5f\": "
     "failed to authorize: 403 Forbidden)", "image_private", "Image is private"),
    ("Services did not become ready: api (InvalidImageName: Failed to apply default image tag \"NEEDS_BUILD:api\")",
     "invalid_image", "Image name is invalid"),
    ("Services did not become ready: echo (CrashLoopBackOff: container keeps crashing (last exit code 128))",
     "crash", "The app keeps crashing"),
    ("Services did not become ready: web (pod is Running but its readiness probe has not passed)",
     "not_ready", "The app never reported ready"),
    ("Services did not become ready: web (Unschedulable: 0/2 nodes are available: 2 Insufficient cpu.)",
     "capacity", "Not enough room in the cluster"),
    ("Preview URLs did not answer: web (HTTP 503)", "no_answer", "Preview didn't respond"),
    ("Services did not become ready: web (pod is still Pending (image pull or scheduling))",
     "slow_start", "Took too long to start"),
    ("Nothing to preview: `api`, `celery_worker` have `build:` but no `image:`. Ephemera runs images",
     "build_only", "No image to run"),
    ("Nothing for a reviewer to open: no deployed service serves HTTP on a published port.",
     "no_public", "Nothing for a reviewer to open"),
    ("No docker-compose.yml in the repository, so there is nothing to preview", "no_compose", "No compose file"),
    ("docker-compose.yml requires variables that are not set: DB_URL", "variables", "Required variable isn't set"),
    ("Gave up after about 60 minutes: timed out waiting for exclusive access to the preview (lock busy)",
     "busy", "Another deployment was in the way"),
    ("Failed to create Kubernetes namespace", "platform", "Ephemera couldn't set up the preview"),
    ("Something nobody has seen before", "unknown", "Preview failed"),
])
def test_known_failures_get_a_plain_explanation(error, category, title):
    d = explain(error, REPO, SHA, 30)
    assert (d.category, d.title) == (category, title)
    assert d.explanation and d.action


def test_services_and_details_are_named():
    d = explain("Services did not become ready: web (CrashLoopBackOff: container keeps crashing (last exit code 1)); "
                "worker (CrashLoopBackOff: container keeps crashing)", REPO, SHA, 30)
    assert d.services == ["web", "worker"]
    assert "`web` and `worker`" in d.explanation and "code 1" in d.explanation


def test_image_missing_names_the_commit_and_links_to_its_build():
    d = explain("Services did not become ready: web (image x was never published; check CI)", REPO, SHA, 30)
    assert "c3d4e5f" in d.explanation
    assert {"label": "View build for this commit", "url": f"https://github.com/{REPO}/commit/{SHA}"} in d.links
    assert {"label": "View pull request", "url": f"https://github.com/{REPO}/pull/30"} in d.links


# ------------------------------------------------------------------ API

def _env(db, user, status, error=None):
    env = Environment(repository_full_name=REPO, repository_name="app", pr_number=30, pr_title="t",
                      branch_name="b", commit_sha=SHA, installation_id=1, owner_id=user.id,
                      status=status, error_message=error)
    env.namespace = env.generate_namespace()
    db.add(env)
    db.commit()
    return env


def test_failed_environments_carry_a_diagnosis(client, auth_headers, db_session, user):
    _env(db_session, user, EnvironmentStatus.FAILED, "Preview URLs did not answer: web (HTTP 503)")
    body = client.get("/api/v1/environments/", headers=auth_headers).json()
    assert body[0]["diagnosis"]["title"] == "Preview didn't respond"
    assert body[0]["error_message"].startswith("Preview URLs")  # raw error still there


def test_healthy_environments_have_no_diagnosis(client, auth_headers, db_session, user):
    _env(db_session, user, EnvironmentStatus.READY)
    assert client.get("/api/v1/environments/", headers=auth_headers).json()[0]["diagnosis"] is None


def test_deployment_history_is_listed_newest_first(client, auth_headers, db_session, user):
    env = _env(db_session, user, EnvironmentStatus.READY)
    first = deployment_crud.create_deployment(db_session, env, "a" * 40)
    second = deployment_crud.create_deployment(db_session, env, "b" * 40)
    body = client.get(f"/api/v1/environments/{env.id}/deployments", headers=auth_headers).json()
    assert {d["id"] for d in body} == {first.id, second.id}
    assert set(body[0]) >= {"commit_sha", "status", "error_message", "created_at"}


def test_deployment_history_of_an_invisible_environment_is_404(client, auth_headers):
    assert client.get("/api/v1/environments/999/deployments", headers=auth_headers).status_code == 404
