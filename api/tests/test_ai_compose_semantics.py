"""
AI-generated manifests are corrected where the compose file fully
determines the answer: the image, and command versus entrypoint.
"""

import yaml

from app.services.ai_deployment import enforce_compose_semantics
from app.services.ai_prompts import SYSTEM_PROMPT


def _deployment(name, **container):
    return {
        "apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name},
        "spec": {"template": {"spec": {"containers": [{"name": name, **container}]}}},
    }


COMPOSE = yaml.safe_load("""
services:
  web:
    build: .
    image: ghcr.io/acme/web:0123456789abcdef
    ports: ["80:80"]
  echo:
    image: hashicorp/http-echo:1.0.0
    command: ["-listen=:5678", "-text=hi"]
  worker:
    image: acme/worker
    entrypoint: /bin/sh -c
    command: "celery -A app worker"
  plain:
    image: nginx
""")


def _container(manifests, name):
    return next(m for m in manifests if m["metadata"]["name"] == name)["spec"]["template"]["spec"]["containers"][0]


def test_compose_command_moved_from_command_to_args():
    # What the model produced on 2026-09-23: exit 128, the runtime tried to exec "-listen=:5678".
    manifests = [_deployment("echo", image="hashicorp/http-echo:1.0.0", command=["-listen=:5678", "-text=hi"])]
    fixes = enforce_compose_semantics(manifests, COMPOSE)
    c = _container(manifests, "echo")
    assert "command" not in c
    assert c["args"] == ["-listen=:5678", "-text=hi"]
    assert fixes and "echo/echo" in fixes[0]


def test_entrypoint_becomes_command_and_string_commands_are_split():
    manifests = [_deployment("worker", image="acme/worker", command=["celery", "-A", "app", "worker"])]
    enforce_compose_semantics(manifests, COMPOSE)
    c = _container(manifests, "worker")
    assert c["command"] == ["/bin/sh", "-c"]
    assert c["args"] == ["celery", "-A", "app", "worker"]


def test_placeholder_or_rewritten_image_is_restored():
    manifests = [
        _deployment("web", image="NEEDS_BUILD:web"),
        _deployment("echo", image="hashicorp/http-echo:latest", args=["-listen=:5678", "-text=hi"]),
    ]
    enforce_compose_semantics(manifests, COMPOSE)
    assert _container(manifests, "web")["image"] == "ghcr.io/acme/web:0123456789abcdef"
    assert _container(manifests, "echo")["image"] == "hashicorp/http-echo:1.0.0"


def test_invented_command_is_removed_when_compose_has_none():
    manifests = [_deployment("plain", image="nginx", command=["nginx", "-g", "daemon off;"])]
    enforce_compose_semantics(manifests, COMPOSE)
    c = _container(manifests, "plain")
    assert "command" not in c and "args" not in c


def test_correct_manifests_are_left_alone():
    manifests = [_deployment("echo", image="hashicorp/http-echo:1.0.0", args=["-listen=:5678", "-text=hi"],
                             readinessProbe={"httpGet": {"path": "/", "port": 5678}})]
    assert enforce_compose_semantics(manifests, COMPOSE) == []
    assert _container(manifests, "echo")["readinessProbe"]["httpGet"]["port"] == 5678


def test_unknown_deployments_and_other_kinds_are_ignored():
    manifests = [
        _deployment("migrate", image="acme/migrate", command=["./migrate"]),
        {"kind": "Service", "metadata": {"name": "echo"}},
    ]
    assert enforce_compose_semantics(manifests, COMPOSE) == []
    assert _container(manifests, "migrate")["command"] == ["./migrate"]


def test_prompt_states_the_mapping():
    assert "docker-compose `command:`" in SYSTEM_PROMPT and "`args`" in SYSTEM_PROMPT
    assert "even when it also has `build:`" in SYSTEM_PROMPT
