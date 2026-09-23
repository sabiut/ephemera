"""
Kubernetes service for managing preview environments.

This service handles:
- Namespace creation and deletion
- Resource quota management
"""

import logging
import time
from typing import Callable, Dict, List, Optional, Tuple

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

    # Outcomes of delete_namespace. Only DELETE_ABSENT means the namespace is
    # gone; DELETE_STARTED means Kubernetes accepted the deletion, which then
    # runs in the background until finalizers complete.
    DELETE_ABSENT = "absent"
    DELETE_STARTED = "deleting"
    DELETE_REFUSED = "refused"
    DELETE_ERROR = "error"

    def is_managed_namespace(self, namespace: str) -> Optional[bool]:
        """
        True if the namespace looks like one Ephemera created: named pr-* and
        carrying our label. False if it exists but is not ours. None only if
        it does not exist. Any other API error is raised, so that a failed
        lookup is never mistaken for a namespace that is already gone.
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
            raise
        labels = (ns.metadata.labels or {})
        return any(all(labels.get(k) == v for k, v in wanted.items()) for wanted in self.MANAGED_LABELS)

    def delete_namespace(self, namespace: str) -> str:
        """
        Ask Kubernetes to delete a namespace Ephemera manages.

        Returns DELETE_ABSENT if it does not exist, DELETE_STARTED if the
        deletion was accepted (it completes asynchronously; confirm with
        wait_for_namespace_gone), DELETE_REFUSED for a namespace that is not
        ours, and DELETE_ERROR if the API could not be asked or refused.
        """
        if not self.enabled:
            logger.warning(f"Kubernetes is disabled, skipping namespace deletion: {namespace}")
            return self.DELETE_ERROR

        try:
            managed = self.is_managed_namespace(namespace)
        except ApiException as e:
            logger.error(f"Could not read namespace {namespace} before deleting it: {e.status} {e.reason}")
            return self.DELETE_ERROR
        except Exception as e:
            logger.error(f"Unexpected error reading namespace {namespace}: {e}")
            return self.DELETE_ERROR
        if managed is None:
            logger.info(f"Namespace {namespace} does not exist")
            return self.DELETE_ABSENT
        if not managed:
            logger.error(f"Refusing to delete namespace {namespace}: not managed by Ephemera")
            return self.DELETE_REFUSED

        try:
            self.core_v1.delete_namespace(name=namespace)
            logger.info(f"Deletion requested for namespace {namespace}")
            return self.DELETE_STARTED
        except ApiException as e:
            if e.status == 404:
                return self.DELETE_ABSENT
            logger.error(f"Failed to delete namespace {namespace}: {e.status} {e.reason}")
            return self.DELETE_ERROR
        except Exception as e:
            logger.error(f"Unexpected error deleting namespace {namespace}: {e}")
            return self.DELETE_ERROR

    def wait_for_namespace_gone(self, namespace: str, timeout_seconds: int = 180, poll_seconds: float = 5.0) -> bool:
        """
        True once the namespace no longer exists. False if it is still there
        at the deadline, or if its existence cannot be determined.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            if self.namespace_exists(namespace) is False:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_seconds)

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

    # Waiting reasons that will not resolve by waiting longer.
    FATAL_WAITING_REASONS = {"InvalidImageName", "ErrImageNeverPull", "CreateContainerConfigError", "CreateContainerError"}
    IMAGE_PULL_REASONS = {"ErrImagePull", "ImagePullBackOff"}
    CRASH_LOOP_RESTARTS = 3
    IMAGE_RETRY_SECONDS = 30

    REVISION_ANNOTATION = "deployment.kubernetes.io/revision"

    @staticmethod
    def _rollout_complete(dep) -> bool:
        """
        The same test ``kubectl rollout status`` applies: the controller has
        seen the latest spec, every pod runs it, no pod of an older rollout
        is left, and the new pods are available.

        Counting ready pods and updated pods separately is not enough: during
        a rolling update the ready pod can be the old one and the updated pod
        not yet ready, so both counts pass while the old version serves. That
        announced ephemera-test-app#25 as Ready on its fourth commit while the
        page still showed the third.
        """
        wanted = dep.spec.replicas if dep.spec and dep.spec.replicas is not None else 1
        status = dep.status
        generation = getattr(dep.metadata, "generation", None) if dep.metadata else None
        observed = getattr(status, "observed_generation", None)
        if generation is not None and (observed is None or observed < generation):
            return False
        updated = getattr(status, "updated_replicas", None) or 0
        total = getattr(status, "replicas", None) or 0
        available = getattr(status, "available_replicas", None)
        if available is None:
            available = getattr(status, "ready_replicas", None) or 0
        return updated >= wanted and total <= updated and available >= updated

    def _pods_for(self, namespace: str, name: str):
        """
        Pods of a Deployment's current rollout only.

        The Deployment's own selector is used because AI-generated manifests
        need not carry the compose converter's ``service`` label. That
        selector also matches pods left over from earlier rollouts, though:
        when a failed preview is re-provisioned into the same namespace, the
        old crash-looping pods are still there, and judging them made the
        wait fail in seconds before the corrected pods had started. So pods
        are narrowed to the ReplicaSet whose revision matches the
        Deployment's current revision, via its pod-template-hash. If that
        ReplicaSet does not exist yet, there are no current pods to judge.
        """
        dep = self.apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
        match = (dep.spec.selector.match_labels if dep.spec and dep.spec.selector else None) or {}
        if not match:
            return []
        # Right after an update the Deployment controller has not yet created
        # the new ReplicaSet or bumped the revision annotation, so "current
        # revision" still names the previous rollout. Judging that rollout's
        # pods failed a retry in 14 seconds on a crash-looping pod the update
        # was replacing. Until the controller has observed this generation,
        # there are no current pods to judge.
        generation = getattr(dep.metadata, "generation", None) if dep.metadata else None
        observed = getattr(dep.status, "observed_generation", None) if dep.status else None
        if generation is not None and (observed is None or observed < generation):
            return []
        selector = ",".join(f"{k}={v}" for k, v in sorted(match.items()))
        pods = self.core_v1.list_namespaced_pod(namespace=namespace, label_selector=selector).items

        revision = ((dep.metadata.annotations or {}) if dep.metadata else {}).get(self.REVISION_ANNOTATION)
        if not revision:
            return pods  # nothing to narrow by; better to judge all than none
        current_hash = None
        for rs in self.apps_v1.list_namespaced_replica_set(namespace=namespace, label_selector=selector).items:
            if ((rs.metadata.annotations or {}).get(self.REVISION_ANNOTATION) == revision
                    and any(ref.name == name for ref in (rs.metadata.owner_references or []))):
                current_hash = (rs.metadata.labels or {}).get("pod-template-hash")
                break
        if not current_hash:
            return []
        return [p for p in pods if (p.metadata.labels or {}).get("pod-template-hash") == current_hash]

    def _pod_state(self, pod, commit_markers):
        """
        Classify a pod as ("image", image) when it is waiting for an image that
        names this commit (CI has probably not pushed it yet), ("fatal", reason)
        when waiting longer will not help, or (None, None).
        """
        statuses = (pod.status.init_container_statuses or []) + (pod.status.container_statuses or [])
        for cs in statuses:
            waiting = cs.state.waiting if cs.state else None
            if not waiting or not waiting.reason:
                continue
            image = cs.image or ""
            if waiting.reason in self.IMAGE_PULL_REASONS:
                if any(m and m in image for m in commit_markers):
                    return "image", image
                continue  # an ordinary pull failure may be transient; the timeout decides
            if waiting.reason in self.FATAL_WAITING_REASONS:
                msg = (waiting.message or "").strip().split("\n")[0]
                return "fatal", f"{waiting.reason}: {msg}" if msg else waiting.reason
            if waiting.reason == "CrashLoopBackOff" and (cs.restart_count or 0) >= self.CRASH_LOOP_RESTARTS:
                last = cs.last_state.terminated if cs.last_state else None
                code = f" (last exit code {last.exit_code})" if last and last.exit_code is not None else ""
                return "fatal", f"CrashLoopBackOff: container keeps crashing{code}"
        return None, None

    def wait_for_deployments_ready(
        self,
        namespace: str,
        names: List[str],
        timeout_seconds: int = 300,
        poll_seconds: float = 5.0,
        image_wait_seconds: int = 0,
        commit_markers: Tuple[str, ...] = (),
        on_waiting_for_image: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[List[str], Dict[str, str]]:
        """
        Block until every named Deployment has all its replicas ready.

        While a pod is failing to pull an image that names this commit, the
        image is assumed to still be building in the repository's CI: the
        deadline is extended by ``image_wait_seconds``, ``on_waiting_for_image``
        is called once, and the pod is deleted every 30 seconds so the
        Deployment retries the pull immediately instead of backing off for
        minutes. Errors that waiting cannot fix (invalid image name, missing
        config, a crash loop) end the wait for that Deployment at once.

        Returns (ready_names, problems) where problems maps each Deployment
        that never became ready to the most useful reason available.
        """
        if not self.enabled:
            logger.warning("Kubernetes is disabled; cannot wait for deployments")
            return [], {name: "Kubernetes is disabled" for name in names}

        pending = set(names)
        ready: List[str] = []
        fatal: Dict[str, str] = {}
        announced = False
        # Once any pod has waited for a commit image, the longer deadline
        # stays in force: after a pod is restarted to retry the pull, its
        # replacement shows ContainerCreating rather than a pull error, and
        # falling back to the short deadline then would give up exactly when
        # the image has arrived.
        image_wait_started = False
        last_kick: Dict[str, float] = {}
        start = time.monotonic()
        deadline = start + timeout_seconds
        image_deadline = deadline + image_wait_seconds

        while pending:
            for name in sorted(pending):
                try:
                    dep = self.apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
                except ApiException as e:
                    if e.status == 404:
                        continue
                    raise
                if self._rollout_complete(dep):
                    ready.append(name)
                    pending.discard(name)
            if not pending:
                break

            waiting_for_image = False
            for name in sorted(pending):
                try:
                    pods = self._pods_for(namespace, name)
                except ApiException:
                    continue
                for pod in pods:
                    kind, detail = self._pod_state(pod, commit_markers)
                    if kind == "fatal":
                        fatal[name] = detail
                        break
                    if kind == "image":
                        waiting_for_image = True
                        image_wait_started = True
                        if not announced and on_waiting_for_image:
                            announced = True
                            try:
                                on_waiting_for_image(name, detail)
                            except Exception as e:  # a status update must never break the deploy
                                logger.warning(f"on_waiting_for_image callback failed: {e}")
                        now = time.monotonic()
                        if now - last_kick.get(name, start) >= self.IMAGE_RETRY_SECONDS:
                            last_kick[name] = now
                            try:
                                self.core_v1.delete_namespaced_pod(name=pod.metadata.name, namespace=namespace)
                                logger.info(f"Retrying image pull for {name} ({detail})")
                            except ApiException as e:
                                logger.warning(f"Could not restart pod {pod.metadata.name}: {e.status}")
            for name in fatal:
                pending.discard(name)
            if not pending:
                break

            if time.monotonic() >= (image_deadline if (waiting_for_image or image_wait_started) else deadline):
                break
            time.sleep(poll_seconds)

        problems = dict(fatal)
        for name in sorted(pending):
            problems[name] = self._deployment_problem(namespace, name, commit_markers)
        return ready, problems

    def _deployment_problem(self, namespace: str, name: str, commit_markers: Tuple[str, ...] = ()) -> str:
        """Best-effort explanation of why a Deployment's pods are not ready."""
        try:
            pods = self._pods_for(namespace, name)
        except ApiException as e:
            return "deployment not found" if e.status == 404 else f"could not inspect pods ({e.status})"
        if not pods:
            try:
                dep = self.apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
            except ApiException:
                return "deployment not found"
            for cond in dep.status.conditions or []:
                if cond.type == "ReplicaFailure" or (cond.type == "Progressing" and cond.status == "False"):
                    return f"{cond.reason}: {cond.message}"
            return "no pods were created"
        for pod in pods:
            statuses = (pod.status.container_statuses or []) + (pod.status.init_container_statuses or [])
            for cs in statuses:
                waiting = cs.state.waiting if cs.state else None
                if waiting and waiting.reason not in (None, "ContainerCreating", "PodInitializing"):
                    if waiting.reason in self.IMAGE_PULL_REASONS and any(m and m in (cs.image or "") for m in commit_markers):
                        return (f"image {cs.image} was never published; check that the repository's CI "
                                "built and pushed it for this commit")
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
