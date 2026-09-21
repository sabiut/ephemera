"""
Kubernetes service for managing preview environments.

This service handles:
- Namespace creation and deletion
- Resource quota management
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class KubernetesService:
    """Service for interacting with Kubernetes cluster"""

    def __init__(self):
        """Initialize Kubernetes client"""
        self.enabled = False
        self.core_v1 = None
        self.apps_v1 = None
        self.networking_v1 = None

        try:
            # Try to load in-cluster config first (for production)
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes configuration")
            self.enabled = True
        except config.ConfigException:
            # Fall back to kubeconfig file (for development)
            try:
                config.load_kube_config()
                logger.info("Loaded Kubernetes configuration from kubeconfig")
                self.enabled = True
            except config.ConfigException as e:
                logger.warning(f"Failed to load Kubernetes configuration: {e}")
                logger.warning("Kubernetes operations will be disabled. This is expected in local development.")
                return

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.networking_v1 = client.NetworkingV1Api()

    def create_namespace(
        self,
        namespace: str,
        labels: Optional[Dict[str, str]] = None
    ) -> bool:
        """Create a namespace. Returns True if it exists afterwards."""
        if not self.enabled:
            logger.warning(f"Kubernetes is disabled, skipping namespace creation: {namespace}")
            return False

        try:
            namespace_obj = client.V1Namespace(
                metadata=client.V1ObjectMeta(name=namespace, labels=labels or {})
            )
            self.core_v1.create_namespace(body=namespace_obj)
            logger.info(f"Created namespace: {namespace}")
            return True

        except ApiException as e:
            if e.status == 409:
                logger.warning(f"Namespace {namespace} already exists")
                return True
            logger.error(f"Failed to create namespace {namespace}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error creating namespace {namespace}: {e}")
            return False

    MANAGED_PREFIX = "pr-"
    MANAGED_LABELS = ({"managed-by": "ephemera"}, {"app": "ephemera"})

    def is_managed_namespace(self, namespace: str) -> Optional[bool]:
        """
        True if the namespace looks like one Ephemera created: named pr-* and
        carrying our label. None if it does not exist or the API failed.
        Guards the cluster-wide delete permission against a bad record or a
        bug ever pointing at kube-system or ephemera-system.
        """
        if not namespace.startswith(self.MANAGED_PREFIX):
            return False
        try:
            ns = self.core_v1.read_namespace(name=namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            logger.error(f"Error reading namespace {namespace}: {e}")
            return None
        labels = (ns.metadata.labels or {})
        return any(all(labels.get(k) == v for k, v in wanted.items()) for wanted in self.MANAGED_LABELS)

    def delete_namespace(self, namespace: str) -> bool:
        """
        Delete a namespace Ephemera manages. Returns True if it is gone or
        being deleted. Refuses (returns False) for unmanaged namespaces.
        """
        if not self.enabled:
            logger.warning(f"Kubernetes is disabled, skipping namespace deletion: {namespace}")
            return False

        managed = self.is_managed_namespace(namespace)
        if managed is None:
            logger.warning(f"Namespace {namespace} not found")
            return True
        if not managed:
            logger.error(f"Refusing to delete namespace {namespace}: not managed by Ephemera")
            return False

        try:
            self.core_v1.delete_namespace(name=namespace)
            logger.info(f"Deleted namespace: {namespace}")
            return True

        except ApiException as e:
            if e.status == 404:
                logger.warning(f"Namespace {namespace} not found")
                return True
            logger.error(f"Failed to delete namespace {namespace}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting namespace {namespace}: {e}")
            return False

    def namespace_exists(self, namespace: str) -> Optional[bool]:
        """
        Check whether a namespace exists.

        Returns True/False when the API answered, or None when the answer is
        unknown (Kubernetes disabled or API error). Callers must not treat
        None as "missing": a transient API failure would otherwise cascade
        into every environment being marked failed.
        """
        if not self.enabled:
            logger.warning(f"Kubernetes is disabled, cannot check namespace: {namespace}")
            return None

        try:
            self.core_v1.read_namespace(name=namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            logger.error(f"Error checking namespace {namespace}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error checking namespace {namespace}: {e}")
            return None

    def create_resource_quota(
        self,
        namespace: str,
        cpu_limit: str = "2",
        memory_limit: str = "4Gi",
        pod_limit: str = "10"
    ) -> bool:
        """Create (or accept an existing) ResourceQuota for a namespace."""
        if not self.enabled:
            logger.warning(f"Kubernetes is disabled, skipping resource quota creation: {namespace}")
            return False

        try:
            quota = client.V1ResourceQuota(
                metadata=client.V1ObjectMeta(name=f"{namespace}-quota", namespace=namespace),
                spec=client.V1ResourceQuotaSpec(
                    hard={
                        "requests.cpu": cpu_limit,
                        "requests.memory": memory_limit,
                        "pods": pod_limit
                    }
                )
            )

            self.core_v1.create_namespaced_resource_quota(namespace=namespace, body=quota)
            logger.info(f"Created resource quota for namespace: {namespace}")
            return True

        except ApiException as e:
            if e.status == 409:
                logger.warning(f"Resource quota already exists for {namespace}")
                return True
            logger.error(f"Failed to create resource quota: {e}")
            return False

    def wait_for_deployments_ready(
        self,
        namespace: str,
        names: List[str],
        timeout_seconds: int = 300,
        poll_seconds: float = 5.0,
    ) -> Tuple[List[str], Dict[str, str]]:
        """
        Block until every named Deployment has all its replicas ready, or the
        timeout passes.

        Returns (ready_names, problems) where problems maps a Deployment that
        never became ready to the most useful reason available: a waiting
        container's reason and message (ImagePullBackOff, CrashLoopBackOff...),
        a failed-scheduling event, or the plain replica count.
        """
        if not self.enabled:
            logger.warning("Kubernetes is disabled; cannot wait for deployments")
            return [], {name: "Kubernetes is disabled" for name in names}

        pending = set(names)
        ready: List[str] = []
        deadline = time.monotonic() + timeout_seconds
        while pending and time.monotonic() < deadline:
            for name in sorted(pending):
                try:
                    dep = self.apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
                except ApiException as e:
                    if e.status == 404:
                        continue
                    raise
                wanted = dep.spec.replicas or 1
                have = dep.status.ready_replicas or 0
                # updated_replicas guards against counting old pods during a rollout
                updated = dep.status.updated_replicas or 0
                if have >= wanted and updated >= wanted:
                    ready.append(name)
                    pending.discard(name)
            if pending:
                time.sleep(poll_seconds)

        problems = {name: self._deployment_problem(namespace, name) for name in sorted(pending)}
        return ready, problems

    def _deployment_problem(self, namespace: str, name: str) -> str:
        """Best-effort explanation of why a Deployment's pods are not ready."""
        try:
            pods = self.core_v1.list_namespaced_pod(namespace=namespace, label_selector=f"service={name}")
        except ApiException as e:
            return f"could not inspect pods ({e.status})"
        if not pods.items:
            try:
                dep = self.apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
            except ApiException:
                return "deployment not found"
            for cond in dep.status.conditions or []:
                if cond.type == "ReplicaFailure" or (cond.type == "Progressing" and cond.status == "False"):
                    return f"{cond.reason}: {cond.message}"
            return "no pods were created"
        for pod in pods.items:
            for cs in (pod.status.container_statuses or []) + (pod.status.init_container_statuses or []):
                waiting = cs.state.waiting if cs.state else None
                if waiting and waiting.reason not in (None, "ContainerCreating", "PodInitializing"):
                    msg = (waiting.message or "").strip().split("\n")[0]
                    return f"{waiting.reason}: {msg}" if msg else waiting.reason
                terminated = cs.state.terminated if cs.state else None
                if terminated and terminated.exit_code not in (None, 0):
                    return f"container exited with code {terminated.exit_code} ({terminated.reason or 'Error'})"
            if pod.status.phase == "Pending":
                for cond in pod.status.conditions or []:
                    if cond.type == "PodScheduled" and cond.status == "False":
                        return f"{cond.reason}: {cond.message}"
                return "pod is still Pending (image pull or scheduling)"
            if pod.status.phase == "Running":
                return "pod is Running but its readiness probe has not passed"
        return "pods did not become ready in time"

    def get_namespace_status(self, namespace: str) -> Optional[str]:
        """Return the namespace phase (Active/Terminating) or None if unknown."""
        if not self.enabled:
            logger.warning(f"Kubernetes is disabled, cannot get namespace status: {namespace}")
            return None

        try:
            ns = self.core_v1.read_namespace(name=namespace)
            return ns.status.phase
        except ApiException as e:
            if e.status == 404:
                return None
            logger.error(f"Error getting namespace status: {e}")
            return None


# Singleton instance
kubernetes_service = KubernetesService()
