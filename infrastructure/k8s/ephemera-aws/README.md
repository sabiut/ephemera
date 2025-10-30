# Ephemera Kubernetes Manifests (AWS/EKS)

This directory will contain Kubernetes manifests to deploy the Ephemera platform to **Amazon Elastic Kubernetes Service (EKS)**.

## Status

**Coming Soon** - AWS deployment is planned for Q4 2024 - Q1 2025.

## Planned Architecture

```
Internet
    ↓
AWS Application Load Balancer (ALB) Ingress Controller
    ↓
ephemera-api.devpreview.app [HTTPS with ACM Certificate]
    ↓
ephemera-api Service (ClusterIP)
    ↓
ephemera-api Pods (2+ replicas)
    ↓
┌─────────────────────┬──────────────────────┬────────────────────┐
│ RDS PostgreSQL      │ ElastiCache Redis    │ EKS Kubernetes API │
│ (Database)          │ (Celery Broker)      │ (Create namespaces)│
└─────────────────────┴──────────────────────┴────────────────────┘
```

## AWS-Specific Features

- **ALB Ingress Controller** instead of nginx-ingress
- **AWS Certificate Manager (ACM)** for TLS certificates
- **IAM Roles for Service Accounts (IRSA)** for pod permissions
- **Elastic Container Registry (ECR)** for Docker images
- **VPC CNI** for pod networking
- **EBS CSI Driver** for persistent volumes (if needed)

## Planned Manifests

- `namespace.yaml` - ephemera-system namespace
- `configmap.yaml` - AWS-specific configuration (AWS_REGION, etc.)
- `secret.yaml` - Sensitive credentials (gitignored)
- `api-deployment.yaml` - API with IRSA annotations
- `celery-worker-deployment.yaml` - Celery workers
- `celery-beat-deployment.yaml` - Celery beat scheduler
- `api-service.yaml` - ClusterIP service
- `api-ingress.yaml` - ALB Ingress with ACM annotations

## Prerequisites (When Implemented)

1. EKS cluster created by Terraform
2. kubectl configured with EKS context
3. AWS Load Balancer Controller installed
4. AWS Certificate Manager certificate for `*.devpreview.app`
5. Route 53 DNS record (or Cloudflare)

## Reference

For detailed multi-cloud strategy, see [MULTI_CLOUD_ARCHITECTURE.md](../../../MULTI_CLOUD_ARCHITECTURE.md).

The GCP/GKE deployment in [infrastructure/k8s/ephemera/](../ephemera/) serves as the reference implementation.
