"""
Services initialization.

This module initializes and exports singleton service instances.
"""

from app.services.kubernetes import kubernetes_service
from app.services.github import github_service
from app.services.deployment import init_deployment_service

# Initialize deployment service with dependencies
deployment_service = init_deployment_service(kubernetes_service, github_service)

__all__ = [
    "kubernetes_service",
    "github_service",
    "deployment_service"
]
