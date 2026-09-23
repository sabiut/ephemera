"""
Deployment service for parsing docker-compose.yml and deploying to Kubernetes.

This service handles:
- Fetching docker-compose.yml from GitHub repositories
- Parsing docker-compose.yml format
- Converting compose services to Kubernetes Deployments, Services and Ingresses
- Applying manifests (of any supported kind) to namespaces
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import yaml
from kubernetes.client.rest import ApiException

from app.services.compose import classify_service, commit_variables, image_report, interpolate

logger = logging.getLogger(__name__)

COMPOSE_FILENAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

# Annotation stamped on every pod template so re-applying the same manifests
# for a new commit still triggers a rollout (and a fresh image pull).
REVISION_ANNOTATION = "ephemera.io/revision"
MANAGED_LABEL, MANAGED_VALUE = "managed-by", "ephemera"
PREVIEW_NAMESPACE_PREFIX = "pr-"
# Ingresses first, so a route disappears before the service behind it.
PRUNABLE_KINDS = ("Ingress", "Service", "Deployment")

DEFAULT_RESOURCES = {
    "requests": {"cpu": "100m", "memory": "128Mi"},
    "limits": {"cpu": "500m", "memory": "512Mi"},
}


def parse_port(port: Any) -> Optional[Tuple[int, int]]:
    """
    Parse a compose port entry into (published, container) ports.

    Accepts "8080:80", "80", 80, "127.0.0.1:8080:80", "8080:80/tcp" and the
    long syntax {"target": 80, "published": 8080}. Returns None if unparseable.
    """
    try:
        if isinstance(port, bool):
            return None
        if isinstance(port, int):
            return port, port
        if isinstance(port, dict):
            target = int(port["target"])
            published = int(port.get("published", target))
            return published, target
        if isinstance(port, str):
            spec = port.split("/")[0]
            parts = spec.split(":")
            container = int(parts[-1])
            published = int(parts[-2]) if len(parts) >= 2 else container
            return published, container
    except (KeyError, ValueError, TypeError):
        pass
    return None


def service_hostname(namespace: str, service_name: str, base_domain: str) -> str:
    """Public hostname for a service: {namespace}-{service}.{base_domain}."""
    return f"{namespace}-{service_name}.{base_domain}"


# Service names that usually mean "the thing a reviewer opens", in order.
PRIMARY_SERVICE_PREFERENCE = ("web", "frontend", "front", "ui", "app", "site", "www", "api")


def choose_primary_url(services: List[str], service_urls: Dict[str, str]) -> Optional[str]:
    """
    The one link to show a reviewer. Prefers conventional front-end names,
    then falls back to the first deployed service that has a URL.
    """
    for candidate in PRIMARY_SERVICE_PREFERENCE:
        if candidate in service_urls:
            return service_urls[candidate]
    for name in services:
        if name in service_urls:
            return service_urls[name]
    return next(iter(service_urls.values()), None)


def probe_urls(
    urls: Dict[str, str],
    timeout_seconds: int = 300,
    poll_seconds: float = 5.0,
) -> Dict[str, str]:
    """
    Wait until every public URL answers over HTTPS, or the timeout passes.

    Any HTTP status below 500 counts as reachable: the application may well
    return 404 on "/" and still be working. TLS errors are expected for the
    first minute while cert-manager issues the certificate, so they are
    retried rather than treated as failures.

    Returns {service: reason} for URLs that never answered.
    """
    import httpx  # local import: keeps this module importable without network deps in tests

    pending = dict(urls)
    last_reason: Dict[str, str] = {}
    deadline = time.monotonic() + timeout_seconds
    while pending and time.monotonic() < deadline:
        for service, url in list(pending.items()):
            try:
                response = httpx.get(url, timeout=10.0, follow_redirects=True)
            except Exception as e:  # connection, TLS, DNS
                last_reason[service] = f"{type(e).__name__}: {e}"[:200]
                continue
            if response.status_code < 500:
                pending.pop(service, None)
            else:
                last_reason[service] = f"HTTP {response.status_code}"
        if pending:
            time.sleep(poll_seconds)
    return {service: last_reason.get(service, "no response") for service in pending}


class DeploymentService:
    """Service for deploying applications to Kubernetes from docker-compose.yml"""

    def __init__(self, kubernetes_service, github_service, base_domain: str = "devpreview.app"):
        self.k8s = kubernetes_service
        self.github = github_service
        self.base_domain = base_domain

    # ------------------------------------------------------------------ fetch

    def fetch_docker_compose(
        self,
        installation_id: int,
        repo_full_name: str,
        ref: str = "HEAD"
    ) -> Optional[str]:
        """Fetch the first compose file found in the repository at ``ref``."""
        try:
            client = self.github.get_installation_client(installation_id)
            if not client:
                logger.error("Cannot fetch docker-compose.yml: GitHub client not configured")
                return None

            repo = client.get_repo(repo_full_name)

            for filename in COMPOSE_FILENAMES:
                try:
                    file_content = repo.get_contents(filename, ref=ref)
                    logger.info(f"Found {filename} in {repo_full_name}")
                    return file_content.decoded_content.decode("utf-8")
                except Exception:
                    continue

            logger.warning(f"No docker-compose.yml found in {repo_full_name}")
            return None

        except Exception as e:
            logger.error(f"Failed to fetch docker-compose.yml: {e}")
            return None

    def parse_docker_compose(self, compose_content: str) -> Optional[Dict[str, Any]]:
        """Parse compose YAML and require a ``services`` mapping."""
        try:
            compose = yaml.safe_load(compose_content)
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse docker-compose.yml: {e}")
            return None

        if not isinstance(compose, dict):
            logger.error("Invalid docker-compose.yml: not a mapping")
            return None

        services = compose.get("services")
        if not isinstance(services, dict) or not services:
            logger.error("Invalid docker-compose.yml: no services defined")
            return None

        logger.info(f"Parsed docker-compose.yml with {len(services)} services")
        return compose

    # ---------------------------------------------------------------- convert

    def convert_compose_to_k8s(
        self,
        compose: Dict[str, Any],
        namespace: str,
        app_name: str
    ) -> List[Dict[str, Any]]:
        """
        Convert compose services to Kubernetes manifests.

        Services that only define ``build:`` (no ``image:``) are skipped, since
        the platform cannot build images; deploying a stand-in image would
        silently misrepresent the preview.
        """
        manifests: List[Dict[str, Any]] = []
        services = compose.get("services", {})

        for service_name, service_config in services.items():
            if not isinstance(service_config, dict):
                logger.warning(f"Skipping service {service_name}: not a mapping")
                continue

            if not service_config.get("image"):
                logger.warning(
                    f"Skipping service {service_name}: no image (build-only services are not supported)"
                )
                continue

            manifests.append(self._create_deployment(service_name, service_config, namespace, app_name))

            ports = [p for p in map(parse_port, service_config.get("ports", []) or []) if p]
            if ports:
                # Every service with ports gets an in-cluster address so the
                # others can reach it by name; only HTTP services get a public
                # route. A database behind an HTTP Ingress fails the URL check
                # and used to sink an otherwise healthy preview.
                manifests.append(self._create_service(service_name, ports, namespace, app_name))
                public, why = classify_service(service_config, [t for _, t in ports])
                if public:
                    manifests.append(self._create_ingress(service_name, ports[0][1], namespace, app_name))
                else:
                    logger.info(f"{service_name}: internal only ({why})")

        logger.info(f"Generated {len(manifests)} Kubernetes manifests")
        return manifests

    def _labels(self, app_name: str, service_name: str) -> Dict[str, str]:
        return {"app": app_name, "service": service_name, "managed-by": "ephemera"}

    def _create_deployment(
        self,
        service_name: str,
        service_config: Dict[str, Any],
        namespace: str,
        app_name: str
    ) -> Dict[str, Any]:
        """Create a Deployment manifest from a compose service."""
        env_vars = []
        env_config = service_config.get("environment", {}) or {}
        if isinstance(env_config, dict):
            for key, value in env_config.items():
                env_vars.append({"name": str(key), "value": "" if value is None else str(value)})
        elif isinstance(env_config, list):
            for env in env_config:
                if isinstance(env, str) and "=" in env:
                    key, value = env.split("=", 1)
                    env_vars.append({"name": key, "value": value})

        container_ports = [
            {"containerPort": target}
            for _, target in (p for p in map(parse_port, service_config.get("ports", []) or []) if p)
        ]

        container: Dict[str, Any] = {
            "name": service_name,
            "image": service_config["image"],
            "env": env_vars,
            "ports": container_ports,
            "resources": DEFAULT_RESOURCES,
        }
        command = service_config.get("command")
        if isinstance(command, str):
            container["args"] = command.split()
        elif isinstance(command, list):
            container["args"] = [str(c) for c in command]

        labels = self._labels(app_name, service_name)
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": service_name, "namespace": namespace, "labels": labels},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": app_name, "service": service_name}},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {"containers": [container]},
                },
            },
        }

    def _create_service(
        self,
        service_name: str,
        ports: List[Tuple[int, int]],
        namespace: str,
        app_name: str
    ) -> Dict[str, Any]:
        """Create a ClusterIP Service manifest."""
        service_ports = [
            {
                "name": f"port-{container}",
                "port": published,
                "targetPort": container,
                "protocol": "TCP",
            }
            for published, container in ports
        ]

        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": service_name,
                "namespace": namespace,
                "labels": self._labels(app_name, service_name),
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {"app": app_name, "service": service_name},
                "ports": service_ports,
            },
        }

    def _create_ingress(
        self,
        service_name: str,
        target_port: int,
        namespace: str,
        app_name: str
    ) -> Dict[str, Any]:
        """Create an Ingress manifest exposing the service over HTTPS."""
        hostname = service_hostname(namespace, service_name, self.base_domain)
        # The Service's `port` is the published port; route the Ingress to it.
        ingress = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{service_name}-ingress",
                "namespace": namespace,
                "labels": self._labels(app_name, service_name),
                "annotations": {
                    "cert-manager.io/cluster-issuer": "letsencrypt-prod",
                    "nginx.ingress.kubernetes.io/ssl-redirect": "true",
                    "nginx.ingress.kubernetes.io/force-ssl-redirect": "true",
                },
            },
            "spec": {
                "ingressClassName": "nginx",
                "tls": [{"hosts": [hostname], "secretName": f"{service_name}-tls"}],
                "rules": [
                    {
                        "host": hostname,
                        "http": {
                            "paths": [
                                {
                                    "path": "/",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": service_name,
                                            "port": {"name": f"port-{target_port}"},
                                        }
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        }
        logger.info(f"Generated Ingress for {service_name} at https://{hostname}")
        return ingress

    # ------------------------------------------------------------------ apply

    def apply_manifest(self, manifest: Dict[str, Any], revision: Optional[str] = None) -> bool:
        """
        Create or update a manifest in the cluster.

        The manifest dict is sent as-is, so probes, volumes, commands and
        resource settings survive. On 409 the existing object is patched.
        Deployments get a revision annotation on the pod template so that a
        redeploy for a new commit restarts the pods.
        """
        if not self.k8s.enabled:
            logger.warning("Kubernetes is disabled, skipping manifest application")
            return False

        kind = manifest.get("kind")
        metadata = manifest.get("metadata", {})
        namespace = metadata.get("namespace")
        name = metadata.get("name")

        # Every object Ephemera applies carries its label, which is how a later
        # deploy finds the ones it no longer wants (see prune_obsolete).
        if kind in PRUNABLE_KINDS:
            metadata.setdefault("labels", {}).setdefault(MANAGED_LABEL, MANAGED_VALUE)

        if kind == "Deployment" and revision:
            template_meta = manifest.setdefault("spec", {}).setdefault("template", {}).setdefault("metadata", {})
            template_meta.setdefault("annotations", {})[REVISION_ANNOTATION] = revision

        api_calls = {
            "Deployment": (self.k8s.apps_v1.create_namespaced_deployment,
                           self.k8s.apps_v1.patch_namespaced_deployment),
            "Service": (self.k8s.core_v1.create_namespaced_service,
                        self.k8s.core_v1.patch_namespaced_service),
            "Ingress": (self.k8s.networking_v1.create_namespaced_ingress,
                        self.k8s.networking_v1.patch_namespaced_ingress),
            "PersistentVolumeClaim": (self.k8s.core_v1.create_namespaced_persistent_volume_claim,
                                      self.k8s.core_v1.patch_namespaced_persistent_volume_claim),
            "ConfigMap": (self.k8s.core_v1.create_namespaced_config_map,
                          self.k8s.core_v1.patch_namespaced_config_map),
            "Secret": (self.k8s.core_v1.create_namespaced_secret,
                       self.k8s.core_v1.patch_namespaced_secret),
        }

        if kind not in api_calls:
            logger.warning(f"Unsupported manifest kind: {kind}")
            return False

        create, patch = api_calls[kind]
        # Deployments are replaced whole on update. A merge patch only changes
        # the fields it names: a field dropped from the new manifest, such as
        # a container command, an environment variable or a probe, silently
        # survived from the previous deployment. That kept a broken command
        # alive through every retry of a failed preview. Other kinds keep
        # patching, since e.g. a Service's clusterIP cannot be replaced.
        update = self.k8s.apps_v1.replace_namespaced_deployment if kind == "Deployment" else patch
        try:
            try:
                create(namespace=namespace, body=manifest)
                logger.info(f"Created {kind} {name} in namespace {namespace}")
            except ApiException as e:
                if e.status != 409:
                    raise
                update(name=name, namespace=namespace, body=manifest)
                logger.info(f"{'Replaced' if kind == 'Deployment' else 'Updated'} {kind} {name} in namespace {namespace}")
            return True
        except Exception as e:
            logger.error(f"Failed to apply {kind}/{name}: {e}")
            return False

    def apply_manifests(
        self, manifests: List[Dict[str, Any]], revision: Optional[str] = None
    ) -> Tuple[int, List[str], Dict[str, str]]:
        """
        Apply a list of manifests.

        Returns (applied_count, failed ["Kind/name"], service_urls {service: url}).
        """
        applied = 0
        failed: List[str] = []
        service_urls: Dict[str, str] = {}

        for manifest in manifests:
            kind = manifest.get("kind", "")
            name = manifest.get("metadata", {}).get("name", "unknown")

            if not self.apply_manifest(manifest, revision=revision):
                failed.append(f"{kind}/{name}")
                continue

            applied += 1
            if kind == "Ingress":
                service_name = manifest.get("metadata", {}).get("labels", {}).get("service") or name
                for rule in manifest.get("spec", {}).get("rules", []):
                    host = rule.get("host")
                    if host:
                        service_urls[service_name] = f"https://{host}"
                        break

        namespaces = {m.get("metadata", {}).get("namespace") for m in manifests}
        if len(namespaces) == 1 and None not in namespaces:
            failed += self.prune_obsolete(namespaces.pop(), manifests)

        return applied, failed, service_urls

    def prune_obsolete(self, namespace: str, manifests: List[Dict[str, Any]]) -> List[str]:
        """
        Delete Ephemera-managed Deployments, Services and Ingresses in the
        namespace that the new manifests no longer contain: a service made
        internal must lose its public route, and one removed from compose must
        stop running. Applying alone only ever creates or updates.

        Returns "Kind/name" for each obsolete object that could not be removed,
        so the deploy is reported as failed rather than leaving a stale route
        live. Only preview namespaces are touched, and only labelled objects.
        """
        if not self.k8s.enabled or not namespace.startswith(PREVIEW_NAMESPACE_PREFIX) or not manifests:
            return []
        wanted = {(m.get("kind"), m.get("metadata", {}).get("name")) for m in manifests}
        selector = f"{MANAGED_LABEL}={MANAGED_VALUE}"
        apis = {
            "Ingress": (self.k8s.networking_v1.list_namespaced_ingress,
                        self.k8s.networking_v1.delete_namespaced_ingress),
            "Service": (self.k8s.core_v1.list_namespaced_service,
                        self.k8s.core_v1.delete_namespaced_service),
            "Deployment": (self.k8s.apps_v1.list_namespaced_deployment,
                           self.k8s.apps_v1.delete_namespaced_deployment),
        }
        not_removed: List[str] = []
        for kind in PRUNABLE_KINDS:
            list_fn, delete_fn = apis[kind]
            try:
                existing = [item.metadata.name for item in list_fn(namespace=namespace, label_selector=selector).items]
            except Exception as e:
                logger.error(f"Could not list {kind}s in {namespace} to remove obsolete ones: {e}")
                not_removed.append(f"{kind}/* (could not check for obsolete objects)")
                continue
            for name in existing:
                if (kind, name) in wanted:
                    continue
                try:
                    delete_fn(name=name, namespace=namespace)
                    logger.info(f"Removed obsolete {kind} {name} from {namespace}")
                except ApiException as e:
                    if e.status == 404:
                        continue
                    logger.error(f"Could not remove obsolete {kind} {name} from {namespace}: {e}")
                    not_removed.append(f"{kind}/{name} (obsolete, not removed)")
                except Exception as e:
                    logger.error(f"Could not remove obsolete {kind} {name} from {namespace}: {e}")
                    not_removed.append(f"{kind}/{name} (obsolete, not removed)")
        return not_removed

    def deploy_application(
        self,
        installation_id: int,
        repo_full_name: str,
        namespace: str,
        ref: str = "HEAD"
    ) -> Dict[str, Any]:
        """
        Deploy an application to a namespace from its docker-compose.yml.

        Returns a dict with: success, compose_found, services, service_urls,
        applied_count, error.
        """
        try:
            app_name = repo_full_name.split("/")[-1].lower().replace("_", "-")

            logger.info(f"Fetching docker-compose.yml from {repo_full_name}@{ref}")
            compose_content = self.fetch_docker_compose(installation_id, repo_full_name, ref)
            if not compose_content:
                return {
                    "success": False,
                    "compose_found": False,
                    "error": "docker-compose.yml not found in repository",
                    "services": [],
                    "service_urls": {},
                }

            interpolated = interpolate(compose_content, commit_variables(ref))
            if interpolated.errors:
                return {
                    "success": False,
                    "compose_found": True,
                    "error": "docker-compose.yml requires variables that are not set: " + "; ".join(interpolated.errors),
                    "services": [],
                    "service_urls": {},
                }
            compose = self.parse_docker_compose(interpolated.text)
            if not compose:
                return {
                    "success": False,
                    "compose_found": True,
                    "error": "Failed to parse docker-compose.yml",
                    "services": [],
                    "service_urls": {},
                }

            manifests = self.convert_compose_to_k8s(compose, namespace, app_name)
            deployed_services = [
                m["metadata"]["name"] for m in manifests if m.get("kind") == "Deployment"
            ]
            skipped = sorted(set(compose["services"]) - set(deployed_services))

            applied_count, failed, service_urls = self.apply_manifests(manifests, revision=ref)

            report = image_report(compose, ref)
            result: Dict[str, Any] = {
                "success": not failed,
                "compose_found": True,
                "applied_count": applied_count,
                "services": deployed_services,
                "skipped_services": skipped,
                "service_urls": service_urls,
                "images": report.images,
                "unpinned_builds": report.unpinned_builds,
                "unset_variables": interpolated.unset,
                "error": f"Failed to apply manifests: {', '.join(failed)}" if failed else None,
            }
            if failed:
                logger.warning(result["error"])
            else:
                logger.info(f"Successfully deployed {applied_count} manifests to {namespace}")
            return result

        except Exception as e:
            logger.error(f"Failed to deploy application: {e}", exc_info=True)
            return {
                "success": False,
                "compose_found": True,
                "error": str(e),
                "services": [],
                "service_urls": {},
            }


# Create singleton instance (will be initialized with services later)
deployment_service = None


def init_deployment_service(kubernetes_service, github_service, base_domain: str = "devpreview.app"):
    """Initialize the deployment service singleton."""
    global deployment_service
    deployment_service = DeploymentService(kubernetes_service, github_service, base_domain)
    return deployment_service
