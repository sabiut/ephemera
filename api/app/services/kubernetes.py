"""
Kubernetes service for managing preview environments.

This service handles:
- Namespace creation and deletion
- Resource quota management
- Deployment manifest application
"""

import logging
from typing import Optional, Dict, Any
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

    def create_namespace(
        self,
        namespace: str,
        labels: Optional[Dict[str, str]] = None
    ) -> bool:
        """
        Create a Kubernetes namespace.

        Args:
            namespace: Name of the namespace
            labels: Optional labels to add to the namespace

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            logger.warning(f"Kubernetes is disabled, skipping namespace creation: {namespace}")
            return False

        try:
            # Prepare namespace metadata
            metadata = client.V1ObjectMeta(
                name=namespace,
                labels=labels or {}
            )

            # Create namespace object
            namespace_obj = client.V1Namespace(
                metadata=metadata
            )

            # Create the namespace
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

    def delete_namespace(self, namespace: str) -> bool:
        """
        Delete a Kubernetes namespace.

        Args:
            namespace: Name of the namespace to delete

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            logger.warning(f"Kubernetes is disabled, skipping namespace deletion: {namespace}")
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

    def namespace_exists(self, namespace: str) -> bool:
        """
        Check if a namespace exists.

        Args:
            namespace: Name of the namespace

        Returns:
            True if namespace exists, False otherwise
        """
        if not self.enabled:
            logger.warning(f"Kubernetes is disabled, cannot check namespace: {namespace}")
            return False

        try:
            self.core_v1.read_namespace(name=namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            logger.error(f"Error checking namespace {namespace}: {e}")
            return False

    def create_resource_quota(
        self,
        namespace: str,
        cpu_limit: str = "2",
        memory_limit: str = "4Gi",
        pod_limit: str = "10"
    ) -> bool:
        """
        Create resource quota for a namespace.

        Args:
            namespace: Name of the namespace
            cpu_limit: CPU limit (e.g., "2" for 2 cores)
            memory_limit: Memory limit (e.g., "4Gi")
            pod_limit: Maximum number of pods

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            logger.warning(f"Kubernetes is disabled, skipping resource quota creation: {namespace}")
            return False

        try:
            quota = client.V1ResourceQuota(
                metadata=client.V1ObjectMeta(
                    name=f"{namespace}-quota",
                    namespace=namespace
                ),
                spec=client.V1ResourceQuotaSpec(
                    hard={
                        "requests.cpu": cpu_limit,
                        "requests.memory": memory_limit,
                        "pods": pod_limit
                    }
                )
            )

            self.core_v1.create_namespaced_resource_quota(
                namespace=namespace,
                body=quota
            )
            logger.info(f"Created resource quota for namespace: {namespace}")
            return True

        except ApiException as e:
            if e.status == 409:
                logger.warning(f"Resource quota already exists for {namespace}")
                return True
            logger.error(f"Failed to create resource quota: {e}")
            return False

    def get_namespace_status(self, namespace: str) -> Optional[str]:
        """
        Get the status of a namespace.

        Args:
            namespace: Name of the namespace

        Returns:
            Status string or None if not found
        """
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
