"""
Deployment service for parsing docker-compose.yml and deploying to Kubernetes.

This service handles:
- Fetching docker-compose.yml from GitHub repositories
- Parsing docker-compose.yml format
- Converting compose services to Kubernetes Deployments and Services
- Applying manifests to namespaces
"""

import logging
import yaml
import base64
from typing import Optional, Dict, Any, List
from kubernetes import client
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class DeploymentService:
    """Service for deploying applications to Kubernetes from docker-compose.yml"""

    def __init__(self, kubernetes_service, github_service):
        """
        Initialize deployment service.

        Args:
            kubernetes_service: KubernetesService instance
            github_service: GitHubService instance
        """
        self.k8s = kubernetes_service
        self.github = github_service

    def fetch_docker_compose(
        self,
        installation_id: int,
        repo_full_name: str,
        ref: str = "HEAD"
    ) -> Optional[str]:
        """
        Fetch docker-compose.yml from a GitHub repository.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Full repository name (owner/repo)
            ref: Git ref (branch, tag, or commit SHA)

        Returns:
            docker-compose.yml content as string, or None if not found
        """
        try:
            client = self.github.get_installation_client(installation_id)
            if not client:
                logger.error("Cannot fetch docker-compose.yml: GitHub client not configured")
                return None

            repo = client.get_repo(repo_full_name)

            # Try common docker-compose file names
            compose_files = [
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml"
            ]

            for filename in compose_files:
                try:
                    file_content = repo.get_contents(filename, ref=ref)
                    content = base64.b64decode(file_content.content).decode('utf-8')
                    logger.info(f"Found {filename} in {repo_full_name}")
                    return content
                except Exception:
                    continue

            logger.warning(f"No docker-compose.yml found in {repo_full_name}")
            return None

        except Exception as e:
            logger.error(f"Failed to fetch docker-compose.yml: {e}")
            return None

    def parse_docker_compose(self, compose_content: str) -> Optional[Dict[str, Any]]:
        """
        Parse docker-compose.yml content.

        Args:
            compose_content: docker-compose.yml file content

        Returns:
            Parsed compose dictionary or None if invalid
        """
        try:
            compose = yaml.safe_load(compose_content)

            # Validate compose format
            if not isinstance(compose, dict):
                logger.error("Invalid docker-compose.yml: not a dictionary")
                return None

            if "services" not in compose:
                logger.error("Invalid docker-compose.yml: no services defined")
                return None

            logger.info(f"Parsed docker-compose.yml with {len(compose['services'])} services")
            return compose

        except yaml.YAMLError as e:
            logger.error(f"Failed to parse docker-compose.yml: {e}")
            return None

    def convert_compose_to_k8s(
        self,
        compose: Dict[str, Any],
        namespace: str,
        app_name: str
    ) -> List[Dict[str, Any]]:
        """
        Convert docker-compose services to Kubernetes manifests.

        Args:
            compose: Parsed docker-compose dictionary
            namespace: Kubernetes namespace
            app_name: Application name for labels

        Returns:
            List of Kubernetes manifest dictionaries
        """
        manifests = []
        services = compose.get("services", {})

        for service_name, service_config in services.items():
            # Generate Deployment manifest
            deployment = self._create_deployment(
                service_name=service_name,
                service_config=service_config,
                namespace=namespace,
                app_name=app_name
            )
            manifests.append(deployment)

            # Generate Service manifest if ports are exposed
            if "ports" in service_config:
                service = self._create_service(
                    service_name=service_name,
                    service_config=service_config,
                    namespace=namespace,
                    app_name=app_name
                )
                manifests.append(service)

        logger.info(f"Generated {len(manifests)} Kubernetes manifests")
        return manifests

    def _create_deployment(
        self,
        service_name: str,
        service_config: Dict[str, Any],
        namespace: str,
        app_name: str
    ) -> Dict[str, Any]:
        """Create Kubernetes Deployment manifest from compose service."""
        # Extract image
        image = service_config.get("image", "nginx:latest")

        # Extract environment variables
        env_vars = []
        env_config = service_config.get("environment", {})

        if isinstance(env_config, dict):
            for key, value in env_config.items():
                env_vars.append({"name": key, "value": str(value)})
        elif isinstance(env_config, list):
            for env in env_config:
                if "=" in env:
                    key, value = env.split("=", 1)
                    env_vars.append({"name": key, "value": value})

        # Extract ports
        container_ports = []
        ports_config = service_config.get("ports", [])

        for port in ports_config:
            if isinstance(port, str):
                # Format: "8000:8000" or "8000"
                parts = port.split(":")
                container_port = int(parts[-1])
                container_ports.append({"containerPort": container_port})
            elif isinstance(port, int):
                container_ports.append({"containerPort": port})

        # Build deployment manifest
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": service_name,
                "namespace": namespace,
                "labels": {
                    "app": app_name,
                    "service": service_name
                }
            },
            "spec": {
                "replicas": 1,
                "selector": {
                    "matchLabels": {
                        "app": app_name,
                        "service": service_name
                    }
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": app_name,
                            "service": service_name
                        }
                    },
                    "spec": {
                        "containers": [{
                            "name": service_name,
                            "image": image,
                            "env": env_vars,
                            "ports": container_ports
                        }]
                    }
                }
            }
        }

        return deployment

    def _create_service(
        self,
        service_name: str,
        service_config: Dict[str, Any],
        namespace: str,
        app_name: str
    ) -> Dict[str, Any]:
        """Create Kubernetes Service manifest from compose service."""
        # Extract ports
        service_ports = []
        ports_config = service_config.get("ports", [])

        for port in ports_config:
            if isinstance(port, str):
                # Format: "8000:8000" or "8000"
                parts = port.split(":")
                if len(parts) == 2:
                    host_port = int(parts[0])
                    container_port = int(parts[1])
                else:
                    host_port = container_port = int(parts[0])

                service_ports.append({
                    "name": f"port-{container_port}",
                    "port": host_port,
                    "targetPort": container_port,
                    "protocol": "TCP"
                })
            elif isinstance(port, int):
                service_ports.append({
                    "name": f"port-{port}",
                    "port": port,
                    "targetPort": port,
                    "protocol": "TCP"
                })

        # Build service manifest
        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": service_name,
                "namespace": namespace,
                "labels": {
                    "app": app_name,
                    "service": service_name
                }
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {
                    "app": app_name,
                    "service": service_name
                },
                "ports": service_ports
            }
        }

        return service

    def apply_manifest(self, manifest: Dict[str, Any]) -> bool:
        """
        Apply a Kubernetes manifest to the cluster.

        Args:
            manifest: Kubernetes manifest dictionary

        Returns:
            True if successful, False otherwise
        """
        if not self.k8s.enabled:
            logger.warning("Kubernetes is disabled, skipping manifest application")
            return False

        try:
            kind = manifest.get("kind")
            namespace = manifest["metadata"]["namespace"]
            name = manifest["metadata"]["name"]
            spec = manifest.get("spec", {})
            metadata = manifest.get("metadata", {})

            if kind == "Deployment":
                # Build Deployment using proper Kubernetes objects
                deployment = client.V1Deployment(
                    api_version="apps/v1",
                    kind="Deployment",
                    metadata=client.V1ObjectMeta(
                        name=name,
                        namespace=namespace,
                        labels=metadata.get("labels", {})
                    ),
                    spec=client.V1DeploymentSpec(
                        replicas=spec.get("replicas", 1),
                        selector=client.V1LabelSelector(
                            match_labels=spec.get("selector", {}).get("matchLabels", {})
                        ),
                        template=client.V1PodTemplateSpec(
                            metadata=client.V1ObjectMeta(
                                labels=spec.get("template", {}).get("metadata", {}).get("labels", {})
                            ),
                            spec=client.V1PodSpec(
                                containers=[
                                    client.V1Container(
                                        name=container.get("name"),
                                        image=container.get("image"),
                                        env=[
                                            client.V1EnvVar(name=env.get("name"), value=env.get("value"))
                                            for env in container.get("env", [])
                                        ],
                                        ports=[
                                            client.V1ContainerPort(container_port=port.get("containerPort"))
                                            for port in container.get("ports", [])
                                        ],
                                        resources=client.V1ResourceRequirements(
                                            requests={
                                                "cpu": "100m",
                                                "memory": "128Mi"
                                            },
                                            limits={
                                                "cpu": "500m",
                                                "memory": "512Mi"
                                            }
                                        )
                                    )
                                    for container in spec.get("template", {}).get("spec", {}).get("containers", [])
                                ]
                            )
                        )
                    )
                )

                try:
                    self.k8s.apps_v1.create_namespaced_deployment(
                        namespace=namespace,
                        body=deployment
                    )
                    logger.info(f"Created Deployment {name} in namespace {namespace}")
                except ApiException as e:
                    if e.status == 409:
                        # Already exists, update it
                        self.k8s.apps_v1.patch_namespaced_deployment(
                            name=name,
                            namespace=namespace,
                            body=deployment
                        )
                        logger.info(f"Updated Deployment {name} in namespace {namespace}")
                    else:
                        raise

            elif kind == "Service":
                # Build Service using proper Kubernetes objects
                service = client.V1Service(
                    api_version="v1",
                    kind="Service",
                    metadata=client.V1ObjectMeta(
                        name=name,
                        namespace=namespace,
                        labels=metadata.get("labels", {})
                    ),
                    spec=client.V1ServiceSpec(
                        type=spec.get("type", "ClusterIP"),
                        selector=spec.get("selector", {}),
                        ports=[
                            client.V1ServicePort(
                                name=port.get("name"),
                                port=port.get("port"),
                                target_port=port.get("targetPort"),
                                protocol=port.get("protocol", "TCP")
                            )
                            for port in spec.get("ports", [])
                        ]
                    )
                )

                try:
                    self.k8s.core_v1.create_namespaced_service(
                        namespace=namespace,
                        body=service
                    )
                    logger.info(f"Created Service {name} in namespace {namespace}")
                except ApiException as e:
                    if e.status == 409:
                        # Already exists, update it
                        self.k8s.core_v1.patch_namespaced_service(
                            name=name,
                            namespace=namespace,
                            body=service
                        )
                        logger.info(f"Updated Service {name} in namespace {namespace}")
                    else:
                        raise

            else:
                logger.warning(f"Unsupported manifest kind: {kind}")
                return False

            return True

        except Exception as e:
            logger.error(f"Failed to apply manifest: {e}")
            return False

    def deploy_application(
        self,
        installation_id: int,
        repo_full_name: str,
        namespace: str,
        ref: str = "HEAD"
    ) -> Dict[str, Any]:
        """
        Deploy an application to Kubernetes from docker-compose.yml.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Full repository name (owner/repo)
            namespace: Kubernetes namespace to deploy to
            ref: Git ref (branch, tag, or commit SHA)

        Returns:
            Deployment result dictionary with success status and details
        """
        try:
            # Extract app name from repo
            app_name = repo_full_name.split("/")[-1].lower().replace("_", "-")

            # Fetch docker-compose.yml
            logger.info(f"Fetching docker-compose.yml from {repo_full_name}@{ref}")
            compose_content = self.fetch_docker_compose(
                installation_id=installation_id,
                repo_full_name=repo_full_name,
                ref=ref
            )

            if not compose_content:
                return {
                    "success": False,
                    "error": "docker-compose.yml not found in repository"
                }

            # Parse docker-compose.yml
            compose = self.parse_docker_compose(compose_content)
            if not compose:
                return {
                    "success": False,
                    "error": "Failed to parse docker-compose.yml"
                }

            # Convert to Kubernetes manifests
            manifests = self.convert_compose_to_k8s(
                compose=compose,
                namespace=namespace,
                app_name=app_name
            )

            # Apply manifests
            applied_count = 0
            failed_manifests = []

            for manifest in manifests:
                success = self.apply_manifest(manifest)
                if success:
                    applied_count += 1
                else:
                    failed_manifests.append(
                        f"{manifest['kind']}/{manifest['metadata']['name']}"
                    )

            if failed_manifests:
                return {
                    "success": False,
                    "error": f"Failed to apply manifests: {', '.join(failed_manifests)}",
                    "applied_count": applied_count
                }

            logger.info(f"Successfully deployed {applied_count} manifests to {namespace}")
            return {
                "success": True,
                "applied_count": applied_count,
                "services": list(compose.get("services", {}).keys())
            }

        except Exception as e:
            logger.error(f"Failed to deploy application: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Create singleton instance (will be initialized with services later)
deployment_service = None


def init_deployment_service(kubernetes_service, github_service):
    """Initialize the deployment service singleton."""
    global deployment_service
    deployment_service = DeploymentService(kubernetes_service, github_service)
    return deployment_service
