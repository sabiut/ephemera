from types import SimpleNamespace

from kubernetes.client.rest import ApiException

from app.services.kubernetes import KubernetesService


class FakeCore:
    def __init__(self, namespaces, read_error=None):
        self.namespaces = namespaces
        self.deleted = []
        self.read_error = read_error

    def read_namespace(self, name):
        if self.read_error:
            raise ApiException(status=self.read_error)
        if name not in self.namespaces:
            raise ApiException(status=404)
        return SimpleNamespace(metadata=SimpleNamespace(labels=self.namespaces[name]))

    def delete_namespace(self, name):
        self.deleted.append(name)


def _service(namespaces, read_error=None):
    svc = KubernetesService.__new__(KubernetesService)
    svc.enabled = True
    svc.core_v1 = FakeCore(namespaces, read_error)
    return svc


def test_only_managed_namespaces_are_deleted():
    svc = _service({
        "pr-1-app": {"managed-by": "ephemera"},
        "pr-2-app": {"app": "ephemera"},          # older label set
        "pr-3-app": {},                           # right prefix, wrong labels
        "ephemera-system": {"app": "ephemera"},   # wrong prefix
        "kube-system": {},
    })
    K = KubernetesService
    assert svc.delete_namespace("pr-1-app") == K.DELETE_STARTED
    assert svc.delete_namespace("pr-2-app") == K.DELETE_STARTED
    assert svc.delete_namespace("pr-3-app") == K.DELETE_REFUSED
    assert svc.delete_namespace("ephemera-system") == K.DELETE_REFUSED
    assert svc.delete_namespace("kube-system") == K.DELETE_REFUSED
    assert svc.core_v1.deleted == ["pr-1-app", "pr-2-app"]


def test_missing_namespace_counts_as_deleted():
    svc = _service({})
    assert svc.delete_namespace("pr-9-gone") == KubernetesService.DELETE_ABSENT
    assert svc.core_v1.deleted == []


def test_api_error_is_not_mistaken_for_a_missing_namespace():
    # The review reproduced delete_namespace() returning True with zero
    # deletion calls when the lookup got HTTP 500.
    svc = _service({"pr-1-app": {"managed-by": "ephemera"}}, read_error=500)
    assert svc.delete_namespace("pr-1-app") == KubernetesService.DELETE_ERROR
    assert svc.core_v1.deleted == []


def test_waits_until_the_namespace_is_really_gone(monkeypatch):
    svc = _service({"pr-1-app": {"managed-by": "ephemera"}})
    checks = iter([True, True, False])
    monkeypatch.setattr(svc, "namespace_exists", lambda ns: next(checks))
    assert svc.wait_for_namespace_gone("pr-1-app", timeout_seconds=5, poll_seconds=0) is True


def test_unknown_existence_never_counts_as_gone(monkeypatch):
    svc = _service({})
    monkeypatch.setattr(svc, "namespace_exists", lambda ns: None)  # API errors
    assert svc.wait_for_namespace_gone("pr-1-app", timeout_seconds=0, poll_seconds=0) is False
