"""
Deploying the new manifests also removes what they no longer contain: a
service made internal loses its public route, and one removed from compose
stops running. Applying alone only ever created or updated.
"""

from types import SimpleNamespace

from kubernetes.client.rest import ApiException

from app.services.deployment import DeploymentService

NS = "pr-1-app"


class FakeCluster:
    """Objects keyed by (kind, name), with the create/patch/replace/list/delete calls used."""

    KINDS = {"deployment": "Deployment", "service": "Service", "ingress": "Ingress",
             "config_map": "ConfigMap", "secret": "Secret", "persistent_volume_claim": "PersistentVolumeClaim"}

    def __init__(self, objects=(), fail_delete=()):
        self.objects = {(o["kind"], o["metadata"]["name"]): o for o in objects}
        self.deleted, self.fail_delete = [], set(fail_delete)

    def __getattr__(self, attr):
        verb, _, rest = attr.partition("_namespaced_")
        kind = self.KINDS.get(rest)
        if not kind:
            raise AttributeError(attr)
        if verb == "create":
            def create(namespace, body):
                if (kind, body["metadata"]["name"]) in self.objects:
                    raise ApiException(status=409)
                self.objects[(kind, body["metadata"]["name"])] = body
            return create
        if verb in ("patch", "replace"):
            def update(name, namespace, body):
                self.objects[(kind, name)] = body
            return update
        if verb == "list":
            def list_(namespace, label_selector):
                key, value = label_selector.split("=")
                items = [SimpleNamespace(metadata=SimpleNamespace(name=n))
                         for (k, n), o in self.objects.items()
                         if k == kind and o["metadata"].get("labels", {}).get(key) == value]
                return SimpleNamespace(items=items)
            return list_
        if verb == "delete":
            def delete(name, namespace):
                if (kind, name) in self.fail_delete:
                    raise ApiException(status=500)
                self.deleted.append((kind, name))
                self.objects.pop((kind, name), None)
            return delete
        raise AttributeError(attr)


def _service(cluster):
    k8s = SimpleNamespace(enabled=True, apps_v1=cluster, core_v1=cluster, networking_v1=cluster)
    return DeploymentService(k8s, github_service=None, base_domain="preview.test")


def _compose(public_echo=True):
    echo = {"image": "hashicorp/http-echo", "ports": ["5678:5678"]}
    if not public_echo:
        echo["labels"] = {"ephemera.public": "false"}
    return {"services": {"web": {"image": "nginx", "ports": ["80:80"]}, "echo": echo}}


def _deploy(svc, compose):
    return svc.apply_manifests(svc.convert_compose_to_k8s(compose, NS, "app"), revision="abc")


def test_making_a_service_internal_removes_its_existing_route():
    cluster = FakeCluster()
    svc = _service(cluster)
    _, failed, urls = _deploy(svc, _compose(public_echo=True))
    assert not failed and ("Ingress", "echo-ingress") in cluster.objects

    _, failed, urls = _deploy(svc, _compose(public_echo=False))
    assert not failed
    assert "echo" not in urls
    assert ("Ingress", "echo-ingress") not in cluster.objects      # before: still routing traffic
    assert ("Service", "echo") in cluster.objects          # still reachable inside the namespace
    assert ("Deployment", "echo") in cluster.objects
    assert cluster.deleted == [("Ingress", "echo-ingress")]


def test_a_service_removed_from_compose_is_removed_from_the_cluster():
    cluster = FakeCluster()
    svc = _service(cluster)
    _deploy(svc, _compose())
    _deploy(svc, {"services": {"web": {"image": "nginx", "ports": ["80:80"]}}})
    assert not any(name == "echo" for _, name in cluster.objects)


def test_unlabelled_objects_and_other_kinds_are_left_alone():
    manual = {"kind": "Ingress", "metadata": {"name": "debug", "labels": {}}}
    data = {"kind": "PersistentVolumeClaim", "metadata": {"name": "db-data", "labels": {"managed-by": "ephemera"}}}
    cluster = FakeCluster([manual, data])
    _deploy(_service(cluster), _compose())
    assert ("Ingress", "debug") in cluster.objects
    assert ("PersistentVolumeClaim", "db-data") in cluster.objects


def test_an_obsolete_route_that_cannot_be_removed_fails_the_deploy():
    cluster = FakeCluster(fail_delete=[("Ingress", "echo-ingress")])
    svc = _service(cluster)
    _deploy(svc, _compose(public_echo=True))
    _, failed, _ = _deploy(svc, _compose(public_echo=False))
    assert failed == ["Ingress/echo-ingress (obsolete, not removed)"]


def test_only_preview_namespaces_are_pruned():
    cluster = FakeCluster([{"kind": "Ingress", "metadata": {"name": "api", "labels": {"managed-by": "ephemera"}}}])
    assert _service(cluster).prune_obsolete("ephemera-system", [{"kind": "Service", "metadata": {"name": "x"}}]) == []
    assert cluster.deleted == []
