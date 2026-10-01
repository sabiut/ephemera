"""
The Kubernetes manifests give each process the settings its code reads. A
setting missing from a manifest only shows up in production: the worker
lacked ENCRYPTION_KEY, so every preview of a repository with a registry
token failed, and lacked the dashboard address, so PR comments linked to
localhost.
"""

import pathlib

import pytest
import yaml

K8S = pathlib.Path(__file__).resolve().parents[2] / "infrastructure" / "k8s" / "ephemera"


def _env(file: str):
    docs = [d for d in yaml.safe_load_all((K8S / file).read_text()) if d and d.get("kind") == "Deployment"]
    return {e["name"] for c in docs[0]["spec"]["template"]["spec"]["containers"] for e in c.get("env", [])}


# Settings the Celery worker's code reads (deploys, managed builds, notices).
WORKER = {
    "DATABASE_URL", "REDIS_URL", "SECRET_KEY", "BASE_DOMAIN", "GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY",
    "ENCRYPTION_KEY",              # registry tokens -> preview pull Secret
    "GITHUB_OAUTH_REDIRECT_URI",   # dashboard links in PR comments
    "MANAGED_BUILDS_ENABLED", "MANAGED_BUILDS_ALLOWLIST", "GCP_PROJECT_ID",
}


@pytest.mark.parametrize("name", sorted(WORKER))
def test_the_worker_gets_every_setting_it_reads(name):
    assert name in _env("celery-worker-deployment.yaml")


def test_the_api_and_worker_share_the_settings_both_read():
    api, worker = _env("api-deployment.yaml"), _env("celery-worker-deployment.yaml")
    api_only = {"ADMIN_GITHUB_LOGINS", "GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET"}  # sign-in, API only
    assert api - worker <= api_only
