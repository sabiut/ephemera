"""
Every preview namespace gets the same isolation baseline, and every
Deployment the same hardening, whichever path generated it. Before, the
cluster enforced network policies but none existed, and only AI-generated
manifests were hardened.
"""

import ipaddress
from types import SimpleNamespace

from kubernetes.client.rest import ApiException

from app.services.deployment import DeploymentService, harden_deployment
from app.services.kubernetes import KubernetesService

NS = "pr-1-web-3f9a2c"


class FakeCluster:
    def __init__(self, existing=(), fail=False):
        self.policies = {name: {"old": True} for name in existing}
        self.labels = {}
        self.fail = fail

    def patch_namespace(self, name, body):
        if self.fail:
            raise ApiException(status=403)
        self.labels.update(body["metadata"]["labels"])

    def create_namespaced_network_policy(self, namespace, body):
        if body["metadata"]["name"] in self.policies:
            raise ApiException(status=409)
        self.policies[body["metadata"]["name"]] = body

    def replace_namespaced_network_policy(self, name, namespace, body):
        self.policies[name] = body


def _k8s(cluster):
    svc = KubernetesService.__new__(KubernetesService)
    svc.enabled = True
    svc.core_v1 = svc.networking_v1 = cluster
    return svc


def _policy(name):
    return next(p for p in KubernetesService.network_policies(NS) if p["metadata"]["name"] == name)["spec"]


def test_a_preview_namespace_gets_pod_security_and_network_policies():
    cluster = FakeCluster()
    assert _k8s(cluster).secure_namespace(NS) is True
    assert cluster.labels["pod-security.kubernetes.io/enforce"] == "baseline"
    assert set(cluster.policies) == {"ephemera-default-deny", "ephemera-allow-ingress", "ephemera-allow-egress"}


def test_existing_previews_get_the_current_policies():
    cluster = FakeCluster(existing=["ephemera-allow-egress"])
    assert _k8s(cluster).secure_namespace(NS) is True
    assert "old" not in cluster.policies["ephemera-allow-egress"]  # replaced, not kept


def test_a_namespace_that_cannot_be_secured_is_reported():
    assert _k8s(FakeCluster(fail=True)).secure_namespace(NS) is False


def test_everything_is_denied_unless_allowed():
    deny = _policy("ephemera-default-deny")
    assert deny == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}


def test_only_the_preview_itself_and_the_ingress_controller_can_reach_it():
    rules = _policy("ephemera-allow-ingress")["ingress"]
    assert rules[0] == {"from": [{"podSelector": {}}]}
    assert rules[1] == {"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}}}]}


def _egress_allows(ip: str, port: int) -> bool:
    """Whether some egress rule's ipBlock admits the address on the port."""
    for rule in _policy("ephemera-allow-egress")["egress"]:
        ports = [p["port"] for p in rule.get("ports", [])]
        if ports and port not in ports:
            continue
        for peer in rule.get("to", [{"ipBlock": {"cidr": "0.0.0.0/0"}}]):
            block = peer.get("ipBlock")
            if not block:
                continue
            addr = ipaddress.ip_address(ip)
            if addr in ipaddress.ip_network(block["cidr"]) and not any(
                    addr in ipaddress.ip_network(e) for e in block.get("except", [])):
                return True
    return False


def test_egress_reaches_the_internet_but_not_the_platform():
    assert _egress_allows("140.82.112.3", 443)        # e.g. api.github.com
    assert not _egress_allows("172.30.0.3", 5432)     # Cloud SQL private IP
    assert not _egress_allows("172.30.124.27", 6378)  # Memorystore
    assert not _egress_allows("10.0.0.8", 10250)      # a node's kubelet
    assert not _egress_allows("10.4.2.3", 80)         # another namespace's pod
    assert not _egress_allows("169.254.169.254", 80)  # the metadata server
    assert _egress_allows("169.254.20.10", 53)        # node-local DNS still answers


# ------------------------------------------------------------------ pod hardening

def _deployment(**pod):
    return {"kind": "Deployment", "metadata": {"name": "web", "namespace": NS},
            "spec": {"template": {"spec": {"containers": [{"name": "web", "image": "nginx"}], **pod}}}}


def test_every_deployment_is_hardened_whichever_path_wrote_it():
    m = _deployment()
    assert harden_deployment(m) is None
    pod = m["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["containers"][0]["securityContext"]["allowPrivilegeEscalation"] is False


def test_host_access_and_privileges_are_refused():
    assert "hostNetwork" in harden_deployment(_deployment(hostNetwork=True))
    assert "hostPath" in harden_deployment(_deployment(volumes=[{"name": "h", "hostPath": {"path": "/"}}]))
    priv = _deployment()
    priv["spec"]["template"]["spec"]["containers"][0]["securityContext"] = {"privileged": True}
    assert "privileged" in harden_deployment(priv)
    caps = _deployment()
    caps["spec"]["template"]["spec"]["containers"][0]["securityContext"] = {"capabilities": {"add": ["NET_ADMIN"]}}
    assert "capabilities" in harden_deployment(caps)


def test_the_compose_converter_output_is_hardened_when_applied():
    applied = []

    class Api:
        def __getattr__(self, attr):
            return lambda **kw: applied.append(kw["body"])

    k8s = SimpleNamespace(enabled=True, apps_v1=Api(), core_v1=Api(), networking_v1=Api())
    svc = DeploymentService(k8s, github_service=None, base_domain="preview.test")
    manifests = svc.convert_compose_to_k8s({"services": {"web": {"image": "nginx", "ports": ["80:80"]}}}, NS, "web")
    dep = next(m for m in manifests if m["kind"] == "Deployment")
    assert svc.apply_manifest(dep) is True
    assert applied[0]["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
