# Ephemera Kubernetes Manifests (Azure/AKS)

This directory will contain Kubernetes manifests to deploy the Ephemera platform to **Azure Kubernetes Service (AKS)**.

## Status

**Coming Soon** - Azure deployment is planned for Q2 2025.

## Planned Architecture

```
Internet
    ↓
Azure Application Gateway Ingress Controller
    ↓
ephemera-api.devpreview.app [HTTPS with Azure Key Vault Certificate]
    ↓
ephemera-api Service (ClusterIP)
    ↓
ephemera-api Pods (2+ replicas)
    ↓
┌─────────────────────────────┬──────────────────────────┬────────────────────┐
│ Azure Database for          │ Azure Cache for Redis    │ AKS Kubernetes API │
│ PostgreSQL                  │ (Celery Broker)          │ (Create namespaces)│
└─────────────────────────────┴──────────────────────────┴────────────────────┘
```

## Azure-Specific Features

- **Application Gateway Ingress Controller (AGIC)** for ingress
- **Azure Key Vault** for secrets management
- **Azure Managed Identity** for pod permissions (AAD Pod Identity)
- **Azure Container Registry (ACR)** for Docker images
- **Azure CNI** for pod networking
- **Azure Disk CSI Driver** for persistent volumes (if needed)
- **Azure Monitor** for logging and metrics

## Planned Manifests

- `namespace.yaml` - ephemera-system namespace
- `configmap.yaml` - Azure-specific configuration (AZURE_REGION, etc.)
- `secret.yaml` - Sensitive credentials (gitignored)
- `api-deployment.yaml` - API with Azure managed identity
- `celery-worker-deployment.yaml` - Celery workers
- `celery-beat-deployment.yaml` - Celery beat scheduler
- `api-service.yaml` - ClusterIP service
- `api-ingress.yaml` - AGIC Ingress with Azure annotations

## Prerequisites (When Implemented)

1. AKS cluster created by Terraform
2. kubectl configured with AKS context
3. Application Gateway Ingress Controller installed
4. Azure Key Vault for certificate storage
5. Azure DNS zone (or Cloudflare)

## Reference

For detailed multi-cloud strategy, see [MULTI_CLOUD_ARCHITECTURE.md](../../../MULTI_CLOUD_ARCHITECTURE.md).

The GCP/GKE deployment in [infrastructure/k8s/ephemera/](../ephemera/) serves as the reference implementation.
