"""
Databases, caches and queues keep an in-cluster address but get no public
route, by the same rule in the converter, the AI path and the setup check.
"""

import pytest
import yaml

from app.services import setup_check
from app.services.ai_deployment import drop_internal_ingresses
from app.services.compose import classify_service
from app.services.deployment import DeploymentService
from app.services.github import InstalledRepository
from tests.test_readiness import _deploy_result, environment, wired  # noqa: F401 (fixtures)
import app.tasks.environment as tasks


class FakeK8s:
    enabled = False


def _kinds(compose_text):
    compose = yaml.safe_load(compose_text)
    manifests = DeploymentService(FakeK8s(), None, "preview.test").convert_compose_to_k8s(compose, "pr-1-app", "app")
    return {(m["kind"], m["metadata"]["name"]) for m in manifests}


@pytest.mark.parametrize("cfg,ports,public", [
    ({"image": "postgres:16"}, [5432], False),
    ({"image": "docker.io/library/redis:7-alpine"}, [6379], False),
    ({"image": "bitnami/kafka:3.7"}, [9092], False),
    ({"image": "mongo@sha256:abc"}, [27017], False),
    ({"image": "acme/custom-db"}, [5432], False),               # unknown image, database port
    ({"image": "nginx:alpine"}, [80], True),
    ({"image": "ghcr.io/acme/web:abc"}, [8080], True),
    ({"image": "nginx"}, [], False),                             # nothing published
    ({"image": "postgres", "labels": {"ephemera.public": "true"}}, [5432], True),
    ({"image": "nginx", "labels": ["ephemera.public=false"]}, [80], False),
    ({"image": "localhost:5000/web"}, [3000], True),            # registry port is not a tag
])
def test_classification(cfg, ports, public):
    assert classify_service(cfg, ports)[0] is public


def test_converter_keeps_an_address_but_no_route_for_databases():
    kinds = _kinds("""
services:
  web: {image: nginx, ports: ["8080:80"]}
  db: {image: "postgres:16", ports: ["5432"]}
  cache: {image: "redis:7", ports: ["6379:6379"]}
""")
    assert ("Service", "db") in kinds and ("Service", "cache") in kinds
    ingresses = {name for kind, name in kinds if kind == "Ingress"}
    assert ingresses == {"web-ingress"}


def test_ai_ingresses_for_internal_services_are_removed():
    compose = yaml.safe_load("services:\n  web: {image: nginx, ports: ['80']}\n  db: {image: postgres, ports: ['5432']}\n")

    def ingress(name, backend):
        return {"kind": "Ingress", "metadata": {"name": name}, "spec": {"rules": [{"host": f"{name}.x", "http": {
            "paths": [{"backend": {"service": {"name": backend, "port": {"number": 1}}}}]}}]}}

    manifests = [ingress("web", "web"), ingress("db", "db"), {"kind": "Service", "metadata": {"name": "db"}}]
    removed = drop_internal_ingresses(manifests, compose)
    assert [m["metadata"]["name"] for m in manifests if m["kind"] == "Ingress"] == ["web"]
    assert removed == ["Ingress db for internal db"]


def _check(text):
    repo = InstalledRepository("acme/app", "app", 1, False, "main", "")
    return setup_check.check_repository(repo, fetch=lambda r, ref: ("docker-compose.yml", text))


def test_postgres_only_is_not_ready_for_previews():
    # The review reproduced this configuration being called "Ready for previews".
    report = _check("services:\n  db:\n    image: postgres:16\n    ports: ['5432:5432']\n")
    assert report.ready is False
    assert any(c.title == "Nothing for a reviewer to open" and c.level == "error" for c in report.checks)
    assert report.services[0].public is False


def test_setup_check_explains_internal_services():
    report = _check("services:\n  web: {image: nginx, ports: ['80']}\n  db: {image: postgres, ports: ['5432']}\n")
    assert report.ready is True
    internal = next(c for c in report.checks if c.title == "db: internal service")
    assert "db:5432" in internal.detail and "ephemera.public" in internal.detail


def test_a_preview_with_nothing_public_fails_with_the_same_explanation(db_session, environment, wired):
    wired["deploy"] = _deploy_result(services=["db"], service_urls={})
    result = tasks._run_deployment(db_session, environment.id, 1, "acme/app", environment.namespace, "c" * 40)
    assert result["success"] is False
    assert result["error"].startswith("Nothing for a reviewer to open")
    assert wired["waited_for"] is None
