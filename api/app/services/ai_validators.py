"""
Validation layer for AI-generated Kubernetes manifests.

Validates schema, security constraints, and resource limits before
applying manifests to the cluster. This is the safety gate between
AI output and the Kubernetes API.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of manifest validation."""
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    corrected_manifests: Optional[List[Dict[str, Any]]] = None

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)


class ManifestValidator:
    """Validates AI-generated Kubernetes manifests before applying."""

    ALLOWED_KINDS = {
        "Deployment",
        "Service",
        "Ingress",
        "PersistentVolumeClaim",
        "ConfigMap",
        "Secret",
    }

    ALLOWED_API_VERSIONS = {
        "apps/v1",
        "v1",
        "networking.k8s.io/v1",
    }

    # Security: service types that must not be exposed externally
    INTERNAL_ONLY_SERVICE_TYPES = {"NodePort", "LoadBalancer", "ExternalName"}

    # Ingress annotations the model may set. Anything else is dropped: the
    # ingress-nginx *-snippet annotations inject raw nginx config into the
    # shared controller, and auth-url/proxy-* can redirect or leak traffic.
    ALLOWED_INGRESS_ANNOTATIONS = {
        "cert-manager.io/cluster-issuer",
        "nginx.ingress.kubernetes.io/ssl-redirect",
        "nginx.ingress.kubernetes.io/force-ssl-redirect",
        "nginx.ingress.kubernetes.io/proxy-body-size",
        "nginx.ingress.kubernetes.io/proxy-read-timeout",
        "nginx.ingress.kubernetes.io/proxy-send-timeout",
        "nginx.ingress.kubernetes.io/backend-protocol",
        "nginx.ingress.kubernetes.io/rewrite-target",
        "nginx.ingress.kubernetes.io/use-regex",
    }
    ALLOWED_INGRESS_CLASSES = {None, "nginx"}

    MAX_MANIFESTS = 50
    MAX_REPLICAS = 2
    MAX_CPU_LIMIT_MILLICORES = 2000  # 2 cores
    MAX_MEMORY_LIMIT_MI = 2048  # 2Gi

    # Valid DNS label pattern for K8s resource names
    DNS_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$")

    def __init__(self, base_domain: Optional[str] = None):
        # When set, every Ingress host must be {namespace}-<something>.{base_domain}.
        # This stops a PR's compose file (or a prompt-injected README) from
        # steering the shared ingress controller to hijack another hostname.
        self.base_domain = base_domain

    def validate_all(
        self, manifests: Any, expected_namespace: str
    ) -> ValidationResult:
        """
        Run all validation checks on a list of manifests.

        Returns a ValidationResult with corrected manifests if validation passes.
        Namespace mismatches are corrected (not rejected).
        """
        result = ValidationResult()
        self._expected_namespace = expected_namespace

        # Must be a list
        if not isinstance(manifests, list):
            result.add_error("AI response is not a list of manifests")
            return result

        if len(manifests) == 0:
            result.add_error("No manifests generated")
            return result

        if len(manifests) > self.MAX_MANIFESTS:
            result.add_error(
                f"Too many manifests: {len(manifests)} (max {self.MAX_MANIFESTS})"
            )
            return result

        corrected = []
        for i, manifest in enumerate(manifests):
            corrected_manifest = self._validate_and_correct(
                manifest, expected_namespace, i, result
            )
            if corrected_manifest is not None:
                corrected.append(corrected_manifest)

        if result.is_valid:
            result.corrected_manifests = corrected

        return result

    def _validate_and_correct(
        self,
        manifest: Any,
        expected_namespace: str,
        index: int,
        result: ValidationResult,
    ) -> Optional[Dict[str, Any]]:
        """Validate a single manifest and return corrected version."""
        prefix = f"Manifest[{index}]"

        # Must be a dict
        if not isinstance(manifest, dict):
            result.add_error(f"{prefix}: Not a dictionary")
            return None

        # Required top-level fields
        kind = manifest.get("kind")
        api_version = manifest.get("apiVersion")
        metadata = manifest.get("metadata")

        if not kind:
            result.add_error(f"{prefix}: Missing 'kind'")
            return None

        if not api_version:
            result.add_error(f"{prefix}: Missing 'apiVersion'")
            return None

        if not isinstance(metadata, dict):
            result.add_error(f"{prefix}: Missing or invalid 'metadata'")
            return None

        name = metadata.get("name")
        if not name:
            result.add_error(f"{prefix} ({kind}): Missing 'metadata.name'")
            return None

        # Validate kind
        if kind not in self.ALLOWED_KINDS:
            result.add_error(
                f"{prefix}: Disallowed kind '{kind}'. "
                f"Allowed: {', '.join(sorted(self.ALLOWED_KINDS))}"
            )
            return None

        # Validate apiVersion
        if api_version not in self.ALLOWED_API_VERSIONS:
            result.add_error(
                f"{prefix} ({kind}/{name}): Disallowed apiVersion '{api_version}'"
            )
            return None

        # Validate resource name is a valid DNS label
        if not self.DNS_LABEL_RE.match(name):
            result.add_error(
                f"{prefix} ({kind}/{name}): Invalid resource name. "
                f"Must be a valid DNS label (lowercase alphanumeric and hyphens)."
            )
            return None

        # Force-correct namespace
        actual_ns = metadata.get("namespace")
        if actual_ns != expected_namespace:
            if actual_ns:
                result.add_warning(
                    f"{prefix} ({kind}/{name}): Corrected namespace "
                    f"from '{actual_ns}' to '{expected_namespace}'"
                )
            manifest["metadata"]["namespace"] = expected_namespace

        # Kind-specific validation
        spec = manifest.get("spec", {})

        if kind == "Deployment":
            self._validate_deployment(manifest, prefix, name, spec, result)
        elif kind == "Service":
            self._validate_service(prefix, name, spec, result)
        elif kind == "Ingress":
            self._validate_ingress(prefix, name, spec, result)
            self._filter_ingress_annotations(manifest, prefix, name, result)
        elif kind == "PersistentVolumeClaim":
            self._validate_pvc(prefix, name, spec, result)
        # ConfigMap and Secret have minimal validation needs

        return manifest

    def _validate_deployment(
        self,
        manifest: Dict,
        prefix: str,
        name: str,
        spec: Dict,
        result: ValidationResult,
    ):
        """Validate Deployment-specific fields and security constraints."""
        # Check replicas
        replicas = spec.get("replicas", 1)
        if replicas > self.MAX_REPLICAS:
            result.add_warning(
                f"{prefix} (Deployment/{name}): Capped replicas "
                f"from {replicas} to {self.MAX_REPLICAS}"
            )
            spec["replicas"] = self.MAX_REPLICAS

        # Navigate to containers
        template = spec.get("template", {})
        pod_spec = template.get("spec", {})

        if not isinstance(pod_spec, dict):
            result.add_error(
                f"{prefix} (Deployment/{name}): Missing spec.template.spec"
            )
            return

        # Security: no host-level access
        if pod_spec.get("hostNetwork"):
            result.add_error(
                f"{prefix} (Deployment/{name}): hostNetwork is not allowed"
            )
            return

        if pod_spec.get("hostPID"):
            result.add_error(
                f"{prefix} (Deployment/{name}): hostPID is not allowed"
            )
            return

        if pod_spec.get("hostIPC"):
            result.add_error(
                f"{prefix} (Deployment/{name}): hostIPC is not allowed"
            )
            return

        # Preview pods never need to talk to the Kubernetes API
        pod_spec["automountServiceAccountToken"] = False
        if pod_spec.get("serviceAccountName") not in (None, "default"):
            result.add_error(
                f"{prefix} (Deployment/{name}): custom serviceAccountName is not allowed"
            )
            return

        # Validate containers (init containers get the same checks)
        containers = pod_spec.get("containers", [])
        if not containers:
            result.add_error(
                f"{prefix} (Deployment/{name}): No containers defined"
            )
            return

        for ci, container in enumerate(containers):
            self._validate_container(
                container, f"{prefix} (Deployment/{name}/container[{ci}])", result
            )
        for ci, container in enumerate(pod_spec.get("initContainers", []) or []):
            self._validate_container(
                container, f"{prefix} (Deployment/{name}/initContainer[{ci}])", result
            )

        # Security: no hostPath volumes
        volumes = pod_spec.get("volumes", [])
        for vol in volumes:
            if isinstance(vol, dict) and vol.get("hostPath"):
                result.add_error(
                    f"{prefix} (Deployment/{name}): hostPath volumes are not allowed"
                )
                return

    def _validate_container(
        self, container: Dict, prefix: str, result: ValidationResult
    ):
        """Validate a container spec."""
        if not isinstance(container, dict):
            result.add_error(f"{prefix}: Container is not a dictionary")
            return

        cname = container.get("name")
        if not cname:
            result.add_error(f"{prefix}: Missing container name")
            return

        image = container.get("image")
        if not image:
            result.add_error(f"{prefix}: Missing container image")
            return

        # Warn on NEEDS_BUILD images
        if image.startswith("NEEDS_BUILD:"):
            result.add_warning(
                f"{prefix}: Image '{image}' requires a build step. "
                f"The service will not start until a pre-built image is pushed."
            )

        # Security: no privileged containers, no added capabilities, no host ports
        security_context = container.get("securityContext")
        if not isinstance(security_context, dict):
            security_context = {}
        container["securityContext"] = security_context
        if security_context.get("privileged"):
            result.add_error(
                f"{prefix}: Privileged containers are not allowed"
            )
            return
        caps = security_context.get("capabilities", {})
        if isinstance(caps, dict) and caps.get("add"):
            result.add_error(
                f"{prefix}: Adding Linux capabilities is not allowed ({', '.join(map(str, caps['add']))})"
            )
            return
        # Never allow escalation; set it explicitly rather than trusting the default
        security_context["allowPrivilegeEscalation"] = False

        for port in container.get("ports", []) or []:
            if isinstance(port, dict) and port.get("hostPort"):
                result.add_error(
                    f"{prefix}: hostPort is not allowed"
                )
                return

        # Cap resource limits so one preview cannot starve the cluster
        resources = container.get("resources", {})
        if isinstance(resources, dict):
            limits = resources.get("limits", {})
            if isinstance(limits, dict):
                self._cap_resource_limit(limits, "cpu", prefix, result)
                self._cap_resource_limit(limits, "memory", prefix, result)

    def _cap_resource_limit(
        self,
        limits: Dict[str, Any],
        resource_type: str,
        prefix: str,
        result: ValidationResult,
    ):
        """Clamp a resource limit to the maximum, editing ``limits`` in place."""
        value = limits.get(resource_type)
        if not value:
            return

        try:
            if resource_type == "cpu":
                if self._parse_cpu(value) > self.MAX_CPU_LIMIT_MILLICORES:
                    limits["cpu"] = f"{self.MAX_CPU_LIMIT_MILLICORES}m"
                    result.add_warning(
                        f"{prefix}: CPU limit {value} capped to {limits['cpu']}"
                    )
            elif resource_type == "memory":
                if self._parse_memory_mi(value) > self.MAX_MEMORY_LIMIT_MI:
                    limits["memory"] = f"{self.MAX_MEMORY_LIMIT_MI}Mi"
                    result.add_warning(
                        f"{prefix}: Memory limit {value} capped to {limits['memory']}"
                    )
        except ValueError:
            result.add_warning(
                f"{prefix}: Could not parse {resource_type} limit '{value}'"
            )

    def _validate_service(
        self, prefix: str, name: str, spec: Dict, result: ValidationResult
    ):
        """Validate Service-specific fields."""
        svc_type = spec.get("type", "ClusterIP")
        if svc_type in self.INTERNAL_ONLY_SERVICE_TYPES:
            result.add_error(
                f"{prefix} (Service/{name}): Service type '{svc_type}' "
                f"is not allowed in preview environments. Use ClusterIP."
            )

        ports = spec.get("ports", [])
        if not ports:
            result.add_warning(
                f"{prefix} (Service/{name}): No ports defined"
            )

    def _validate_ingress(
        self, prefix: str, name: str, spec: Dict, result: ValidationResult
    ):
        """Validate Ingress-specific fields, including hostname ownership."""
        rules = spec.get("rules", [])
        if not rules:
            result.add_warning(
                f"{prefix} (Ingress/{name}): No rules defined"
            )

        hosts = [r.get("host") for r in rules if isinstance(r, dict)]
        for tls in spec.get("tls", []) or []:
            if isinstance(tls, dict):
                hosts.extend(tls.get("hosts", []) or [])

        for host in hosts:
            if not host:
                result.add_error(
                    f"{prefix} (Ingress/{name}): Ingress rules must specify a host"
                )
            elif not self.is_allowed_host(host):
                result.add_error(
                    f"{prefix} (Ingress/{name}): Host '{host}' is outside this "
                    f"environment. Allowed: {self._expected_namespace}-<service>.{self.base_domain}"
                )

    def _filter_ingress_annotations(
        self, manifest: Dict, prefix: str, name: str, result: ValidationResult
    ):
        """Drop annotations outside the allowlist and pin the ingress class."""
        annotations = manifest.get("metadata", {}).get("annotations") or {}
        if not isinstance(annotations, dict):
            manifest["metadata"]["annotations"] = {}
            return
        dropped = [k for k in annotations if k not in self.ALLOWED_INGRESS_ANNOTATIONS]
        for key in dropped:
            del annotations[key]
        if dropped:
            result.add_warning(
                f"{prefix} (Ingress/{name}): Removed disallowed annotations: {', '.join(sorted(dropped))}"
            )
        manifest["metadata"]["annotations"] = annotations

        spec = manifest.get("spec", {})
        if spec.get("ingressClassName") not in self.ALLOWED_INGRESS_CLASSES:
            result.add_error(
                f"{prefix} (Ingress/{name}): ingressClassName '{spec.get('ingressClassName')}' is not allowed"
            )

    def is_allowed_host(self, host: str) -> bool:
        """A host is allowed when it is {namespace}-<label>.{base_domain}."""
        if not self.base_domain:
            return True
        pattern = re.compile(
            rf"^{re.escape(self._expected_namespace)}-[a-z0-9]([a-z0-9-]*[a-z0-9])?\.{re.escape(self.base_domain)}$"
        )
        return bool(pattern.match(host.lower()))

    def _validate_pvc(
        self, prefix: str, name: str, spec: Dict, result: ValidationResult
    ):
        """Validate PersistentVolumeClaim-specific fields."""
        access_modes = spec.get("accessModes", [])
        if not access_modes:
            result.add_warning(
                f"{prefix} (PVC/{name}): No accessModes specified"
            )

        resources = spec.get("resources", {})
        requests = resources.get("requests", {})
        if not requests.get("storage"):
            result.add_warning(
                f"{prefix} (PVC/{name}): No storage request specified"
            )

    @staticmethod
    def _parse_cpu(value: str) -> int:
        """Parse CPU value to millicores."""
        value = str(value).strip()
        if value.endswith("m"):
            return int(value[:-1])
        return int(float(value) * 1000)

    @staticmethod
    def _parse_memory_mi(value: str) -> int:
        """Parse memory value to MiB (approximate)."""
        value = str(value).strip()
        if value.endswith("Gi"):
            return int(float(value[:-2]) * 1024)
        if value.endswith("Mi"):
            return int(float(value[:-2]))
        if value.endswith("Ki"):
            return int(float(value[:-2]) / 1024)
        # Assume bytes
        return int(int(value) / (1024 * 1024))
