import yaml

from app.models.environment import build_namespace
from app.services.deployment import DeploymentService, parse_port, service_hostname


class FakeK8s:
    enabled = False


def _service():
    return DeploymentService(FakeK8s(), github_service=None, base_domain="preview.test")


def test_build_namespace_is_a_dns_label():
    assert build_namespace("My_App.Service", 12) == "pr-12-my-app-service"
    assert len(build_namespace("a" * 80, 1)) <= 63


def test_parse_port_variants():
    assert parse_port("8080:80") == (8080, 80)
    assert parse_port("80") == (80, 80)
    assert parse_port(3000) == (3000, 3000)
    assert parse_port("127.0.0.1:8080:80/tcp") == (8080, 80)
    assert parse_port({"target": 80, "published": 8080}) == (8080, 80)
    assert parse_port("garbage") is None


def test_build_only_services_are_skipped():
    compose = yaml.safe_load(
        """
services:
  web:
    build: .
    ports: ["8000:8000"]
  db:
    image: postgres:15
    environment:
      POSTGRES_PASSWORD: x
"""
    )
    manifests = _service().convert_compose_to_k8s(compose, "pr-1-app", "app")
    kinds = [(m["kind"], m["metadata"]["name"]) for m in manifests]
    assert kinds == [("Deployment", "db")]
    container = manifests[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "postgres:15"
    assert container["env"] == [{"name": "POSTGRES_PASSWORD", "value": "x"}]
    assert "resources" in container


def test_hostnames_are_scoped_to_the_namespace():
    compose = yaml.safe_load(
        """
services:
  web:
    image: nginx
    ports: ["8080:80"]
"""
    )
    manifests = _service().convert_compose_to_k8s(compose, "pr-5-app", "app")
    ingress = next(m for m in manifests if m["kind"] == "Ingress")
    svc = next(m for m in manifests if m["kind"] == "Service")

    host = ingress["spec"]["rules"][0]["host"]
    assert host == service_hostname("pr-5-app", "web", "preview.test") == "pr-5-app-web.preview.test"
    assert ingress["spec"]["tls"][0]["hosts"] == [host]

    # Ingress routes to the Service port by name, and that name exists
    backend_port = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"]["name"]
    assert backend_port in {p["name"] for p in svc["spec"]["ports"]}
    assert svc["spec"]["ports"][0] == {"name": "port-80", "port": 8080, "targetPort": 80, "protocol": "TCP"}


def test_parse_rejects_compose_without_services():
    svc = _service()
    assert svc.parse_docker_compose("version: '3'\n") is None
    assert svc.parse_docker_compose("- just\n- a list\n") is None
    assert svc.parse_docker_compose(": not yaml: [") is None
