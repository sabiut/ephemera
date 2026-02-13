"""
Services initialization.

This module initializes and exports singleton service instances.
"""

from app.services.kubernetes import kubernetes_service
from app.services.github import github_service
from app.services.deployment import init_deployment_service
from app.services.ai_deployment import init_ai_deployment_service
from app.config import settings

# Initialize deployment service with dependencies
deployment_service = init_deployment_service(
    kubernetes_service,
    github_service,
    base_domain=settings.base_domain
)

# Initialize AI deployment service (wraps deployment_service with AI capabilities)
ai_deployment_service = init_ai_deployment_service(
    deployment_service=deployment_service,
    github_service=github_service,
    kubernetes_service=kubernetes_service,
    settings=settings,
)

__all__ = [
    "kubernetes_service",
    "github_service",
    "deployment_service",
    "ai_deployment_service",
]
