from app.services.ai_validators import ManifestValidator


def _deployment(name="web", cpu="500m", memory="512Mi", **pod_extra):
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": "pr-1-app"},
        "spec": {
            "replicas": 1,
            "template": {
                "spec": {
                    "containers": [
                        {"name": name, "image": "nginx", "resources": {"limits": {"cpu": cpu, "memory": memory}}}
                    ],
                    **pod_extra,
                }
            },
        },
    }


def _ingress(host, name="web-ingress"):
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": name, "namespace": "pr-1-app"},
        "spec": {"tls": [{"hosts": [host]}], "rules": [{"host": host, "http": {"paths": []}}]},
    }


def test_valid_manifests_pass_and_namespace_is_forced():
    v = ManifestValidator(base_domain="preview.test")
    dep = _deployment()
    dep["metadata"]["namespace"] = "somewhere-else"
    result = v.validate_all([dep, _ingress("pr-1-app-web.preview.test")], "pr-1-app")
    assert result.is_valid, result.errors
    assert result.corrected_manifests[0]["metadata"]["namespace"] == "pr-1-app"
    assert any("Corrected namespace" in w for w in result.warnings)


def test_ingress_host_outside_environment_is_rejected():
    v = ManifestValidator(base_domain="preview.test")
    for host in [
        "ephemera-api.preview.test",      # another app on the same domain
        "pr-2-app-web.preview.test",      # another PR
        "pr-1-app-web.evil.example",      # another domain
        "pr-1-app.preview.test",          # missing the service label
    ]:
        result = v.validate_all([_ingress(host)], "pr-1-app")
        assert not result.is_valid, host


def test_resource_limits_are_capped():
    v = ManifestValidator(base_domain="preview.test")
    result = v.validate_all([_deployment(cpu="8", memory="16Gi")], "pr-1-app")
    assert result.is_valid
    limits = result.corrected_manifests[0]["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]
    assert limits == {"cpu": "2000m", "memory": "2048Mi"}
    assert len(result.warnings) == 2


def test_host_access_is_rejected():
    v = ManifestValidator(base_domain="preview.test")
    assert not v.validate_all([_deployment(hostNetwork=True)], "pr-1-app").is_valid
    assert not v.validate_all([_deployment(volumes=[{"name": "x", "hostPath": {"path": "/"}}])], "pr-1-app").is_valid


def test_disallowed_kinds_and_service_types():
    v = ManifestValidator(base_domain="preview.test")
    bad_kind = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "x"}}
    assert not v.validate_all([bad_kind], "pr-1-app").is_valid

    lb = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "s"}, "spec": {"type": "LoadBalancer", "ports": [{}]}}
    assert not v.validate_all([lb], "pr-1-app").is_valid
