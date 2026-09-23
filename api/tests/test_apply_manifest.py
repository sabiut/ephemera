"""
Updating an existing Deployment must leave exactly the new manifest in the
cluster, so fields dropped from it are removed rather than kept.
"""

from types import SimpleNamespace

from kubernetes.client.rest import ApiException

from app.services.deployment import DeploymentService


class FakeApi:
    def __init__(self):
        self.calls = []

    def _exists(self, **kwargs):
        self.calls.append(("create", kwargs["body"]["kind"]))
        raise ApiException(status=409, reason="AlreadyExists")

    def __getattr__(self, attr):
        if attr.startswith("create_namespaced_"):
            return self._exists
        if attr.startswith(("patch_namespaced_", "replace_namespaced_")):
            verb = attr.split("_")[0]
            return lambda **kw: self.calls.append((verb, kw["body"]["kind"], kw["body"]))
        raise AttributeError(attr)


def _service():
    api = FakeApi()
    k8s = SimpleNamespace(enabled=True, apps_v1=api, core_v1=api, networking_v1=api)
    return DeploymentService(k8s, github_service=None, base_domain="preview.test"), api


def _deployment(**container):
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": "echo", "namespace": "pr-1-app"},
        "spec": {"template": {"spec": {"containers": [{"name": "echo", "image": "hashicorp/http-echo", **container}]}}},
    }


def test_existing_deployment_is_replaced_not_patched():
    svc, api = _service()
    corrected = _deployment(args=["-listen=:5678"])  # no "command": it must be removed in the cluster
    assert svc.apply_manifest(corrected, revision="abc") is True
    verbs = [c[0] for c in api.calls]
    assert verbs == ["create", "replace"]
    sent = api.calls[-1][2]
    container = sent["spec"]["template"]["spec"]["containers"][0]
    assert "command" not in container and container["args"] == ["-listen=:5678"]
    assert sent["spec"]["template"]["metadata"]["annotations"]  # revision annotation still added


def test_other_kinds_still_patch():
    svc, api = _service()
    service = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "echo", "namespace": "pr-1-app"}, "spec": {}}
    assert svc.apply_manifest(service) is True
    assert [c[0] for c in api.calls] == ["create", "patch"]
