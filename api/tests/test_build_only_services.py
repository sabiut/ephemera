"""
A service with build: and no image: has nothing to run. It is skipped on
both deploy paths, never given a placeholder image, and when that leaves
nothing a reviewer could open the deploy stops before touching the cluster.
"""

from types import SimpleNamespace

from app.services.ai_deployment import AIDeploymentService, RepoContext, drop_build_only_services
from app.services.compose import build_only_blocker
from app.services.deployment import DeploymentService

# Ephemera's own local-development compose file, which produced
# InvalidImageName: "NEEDS_BUILD:api" pods on a real PR.
LOCAL_DEV = {"services": {
    "api": {"build": "./api", "ports": ["8000:8000"]},
    "celery_worker": {"build": "./api"},
    "postgres": {"image": "postgres:16", "ports": ["5432:5432"]},
    "redis": {"image": "redis:7", "ports": ["6379:6379"]},
}}
LOCAL_DEV_YAML = """services:
  api:
    build: ./api
    ports: ["8000:8000"]
  celery_worker:
    build: ./api
  postgres:
    image: postgres:16
    ports: ["5432:5432"]
"""


def test_nothing_public_left_blocks_the_deploy_with_what_to_add():
    message = build_only_blocker(LOCAL_DEV)
    assert message.startswith("Nothing to preview: `api`, `celery_worker` have `build:` but no `image:`")
    assert "image: ghcr.io/<owner>/<repo>:${EPHEMERA_SHA}" in message


def test_a_build_only_helper_next_to_a_public_image_does_not_block():
    compose = {"services": {"web": {"image": "nginx", "ports": ["80:80"]}, "tests": {"build": "."}}}
    assert build_only_blocker(compose) is None


def test_no_build_only_services_never_blocks():
    assert build_only_blocker({"services": {"db": {"image": "postgres:16"}}}) is None


def test_converter_stops_before_applying_anything():
    svc = DeploymentService(SimpleNamespace(enabled=True), github_service=None, base_domain="preview.test")
    svc.fetch_docker_compose = lambda *a, **k: LOCAL_DEV_YAML
    applied = []
    svc.apply_manifests = lambda *a, **k: applied.append(a) or (0, [], {})
    result = svc.deploy_application(1, "acme/app", "pr-1-app", ref="a" * 40)
    assert result["success"] is False and result["error"].startswith("Nothing to preview")
    assert result["skipped_services"] == ["api", "celery_worker"]
    assert applied == []  # postgres was not started just to crash-loop


def test_ai_path_stops_before_calling_the_model():
    calls = []
    provider = SimpleNamespace(provider_name="fake", generate=lambda **k: calls.append(k))
    svc = AIDeploymentService(None, None, None, provider=provider)
    svc._fetch_repo_context = lambda *a: RepoContext(compose_content=LOCAL_DEV_YAML, compose_filename="docker-compose.yml")
    result = svc.deploy_application(1, "acme/app", "pr-1-app", ref="a" * 40)
    assert result["success"] is False and result["error"].startswith("Nothing to preview")
    assert calls == []  # no tokens spent


def _deployment(name, image):
    return {"kind": "Deployment", "metadata": {"name": name},
            "spec": {"template": {"spec": {"containers": [{"name": name, "image": image}]}}}}


def _ingress(name, backend):
    return {"kind": "Ingress", "metadata": {"name": name}, "spec": {"rules": [{"http": {"paths": [
        {"backend": {"service": {"name": backend}}}]}}]}}


def test_a_placeholder_on_a_service_with_an_image_is_corrected_not_dropped():
    from app.services.ai_deployment import enforce_compose_semantics
    compose = {"services": {"worker": {"build": ".", "image": "ghcr.io/acme/worker:abc"}}}
    manifests = [_deployment("worker", "NEEDS_BUILD:worker")]
    enforce_compose_semantics(manifests, compose)
    assert drop_build_only_services(manifests, compose) == []
    assert manifests[0]["spec"]["template"]["spec"]["containers"][0]["image"] == "ghcr.io/acme/worker:abc"


def test_placeholder_workloads_and_their_routes_are_dropped():
    compose = {"services": {"web": {"image": "nginx", "ports": ["80:80"]}, "api": {"build": "."}}}
    manifests = [
        _deployment("web", "nginx"), {"kind": "Service", "metadata": {"name": "web"}}, _ingress("web", "web"),
        _deployment("api", "NEEDS_BUILD:api"), {"kind": "Service", "metadata": {"name": "api"}}, _ingress("api", "api"),
        _deployment("worker", "NEEDS_BUILD:worker"),  # not in compose under this name: still no image to run
        {"kind": "ConfigMap", "metadata": {"name": "api"}},
    ]
    assert drop_build_only_services(manifests, compose) == ["api", "worker"]
    assert [(m["kind"], m["metadata"]["name"]) for m in manifests] == [
        ("Deployment", "web"), ("Service", "web"), ("Ingress", "web"), ("ConfigMap", "api")]


def test_nothing_to_drop_leaves_manifests_alone():
    manifests = [_deployment("web", "nginx")]
    assert drop_build_only_services(manifests, {"services": {"web": {"image": "nginx"}}}) == []
    assert len(manifests) == 1
