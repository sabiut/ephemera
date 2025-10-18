"""
Services initialization.

This module initializes and exports singleton service instances.
"""

from app.services.kubernetes import kubernetes_service
from app.services.github import github_service
from app.services.deployment import init_deployment_service
from app.config import settings

# Initialize deployment service with dependencies
deployment_service = init_deployment_service(
    kubernetes_service,
    github_service,
    base_domain=settings.base_domain
)

__all__ = [
    "kubernetes_service",
    "github_service",
    "deployment_service"
]
