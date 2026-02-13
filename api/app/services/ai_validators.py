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

    MAX_MANIFESTS = 50
    MAX_REPLICAS = 2
    MAX_CPU_LIMIT_MILLICORES = 2000  # 2 cores
    MAX_MEMORY_LIMIT_MI = 2048  # 2Gi

    # Valid DNS label pattern for K8s resource names
    DNS_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$")

    def validate_all(
        self, manifests: Any, expected_namespace: str
    ) -> ValidationResult:
        """
        Run all validation checks on a list of manifests.

        Returns a ValidationResult with corrected manifests if validation passes.
        Namespace mismatches are corrected (not rejected).
        """
        result = ValidationResult()

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

        # Validate containers
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

        # Security: no privileged containers
        security_context = container.get("securityContext", {})
        if isinstance(security_context, dict):
            if security_context.get("privileged"):
                result.add_error(
                    f"{prefix}: Privileged containers are not allowed"
                )
                return

        # Validate resource limits are within bounds
        resources = container.get("resources", {})
        if isinstance(resources, dict):
            limits = resources.get("limits", {})
            if isinstance(limits, dict):
                self._check_resource_limit(
                    limits.get("cpu"), "cpu", prefix, result
                )
                self._check_resource_limit(
                    limits.get("memory"), "memory", prefix, result
                )

    def _check_resource_limit(
        self,
        value: Optional[str],
        resource_type: str,
        prefix: str,
        result: ValidationResult,
    ):
        """Check that a resource limit is within bounds."""
        if not value:
            return

        try:
            if resource_type == "cpu":
                millicores = self._parse_cpu(value)
                if millicores > self.MAX_CPU_LIMIT_MILLICORES:
                    result.add_warning(
                        f"{prefix}: CPU limit {value} exceeds maximum "
                        f"{self.MAX_CPU_LIMIT_MILLICORES}m, will be capped"
                    )
            elif resource_type == "memory":
                mi = self._parse_memory_mi(value)
                if mi > self.MAX_MEMORY_LIMIT_MI:
                    result.add_warning(
                        f"{prefix}: Memory limit {value} exceeds maximum "
                        f"{self.MAX_MEMORY_LIMIT_MI}Mi, will be capped"
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
        """Validate Ingress-specific fields."""
        rules = spec.get("rules", [])
        if not rules:
            result.add_warning(
                f"{prefix} (Ingress/{name}): No rules defined"
            )

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
