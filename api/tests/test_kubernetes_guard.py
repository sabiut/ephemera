from types import SimpleNamespace

from kubernetes.client.rest import ApiException

from app.services.kubernetes import KubernetesService


class FakeCore:
    def __init__(self, namespaces):
        self.namespaces = namespaces
        self.deleted = []

    def read_namespace(self, name):
        if name not in self.namespaces:
            raise ApiException(status=404)
        return SimpleNamespace(metadata=SimpleNamespace(labels=self.namespaces[name]))

    def delete_namespace(self, name):
        self.deleted.append(name)


def _service(namespaces):
    svc = KubernetesService.__new__(KubernetesService)
    svc.enabled = True
    svc.core_v1 = FakeCore(namespaces)
    return svc


def test_only_managed_namespaces_are_deleted():
    svc = _service({
        "pr-1-app": {"managed-by": "ephemera"},
        "pr-2-app": {"app": "ephemera"},          # older label set
        "pr-3-app": {},                           # right prefix, wrong labels
        "ephemera-system": {"app": "ephemera"},   # wrong prefix
        "kube-system": {},
    })
    assert svc.delete_namespace("pr-1-app") is True
    assert svc.delete_namespace("pr-2-app") is True
    assert svc.delete_namespace("pr-3-app") is False
    assert svc.delete_namespace("ephemera-system") is False
    assert svc.delete_namespace("kube-system") is False
    assert svc.core_v1.deleted == ["pr-1-app", "pr-2-app"]


def test_missing_namespace_counts_as_deleted():
    svc = _service({})
    assert svc.delete_namespace("pr-9-gone") is True
    assert svc.core_v1.deleted == []
