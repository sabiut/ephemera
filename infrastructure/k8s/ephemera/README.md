# Ephemera Kubernetes Manifests (GCP/GKE)

This directory contains Kubernetes manifests to deploy the Ephemera platform to **Google Kubernetes Engine (GKE)**.

> **Multi-Cloud Note**: Ephemera is designed to be cloud-agnostic. This is the GCP deployment.
> Future AWS (EKS) and Azure (AKS) deployments will follow the same pattern with cloud-specific directories.
> See [MULTI_CLOUD_ARCHITECTURE.md](../../../MULTI_CLOUD_ARCHITECTURE.md) for details.

## Files

- `namespace.yaml` - Creates the ephemera-system namespace
- `configmap.yaml` - Non-sensitive configuration
- `secret.yaml` - Sensitive credentials (NOT committed to Git)
- `secret.yaml.template` - Template for creating secret.yaml
- `api-deployment.yaml` - API server deployment with RBAC
- `celery-worker-deployment.yaml` - Celery worker deployment
- `celery-beat-deployment.yaml` - Celery beat scheduler deployment
- `api-service.yaml` - Service for API
- `api-ingress.yaml` - Ingress for HTTPS access at ephemera-api.devpreview.app

## Prerequisites

1. GKE cluster running (created by Terraform)
2. kubectl configured to access the cluster
3. nginx-ingress controller installed
4. cert-manager installed with Let's Encrypt ClusterIssuer
5. DNS record for `ephemera-api.devpreview.app` pointing to LoadBalancer IP

## Deployment Steps

### 1. Create the Secret

The `secret.yaml` file contains sensitive credentials and is gitignored.

```bash
# It's already created with your credentials from api/.env
# Verify it exists:
ls secret.yaml
```

### 2. Apply Manifests

```bash
# Apply in order:
kubectl apply -f namespace.yaml
kubectl apply -f configmap.yaml
kubectl apply -f secret.yaml
kubectl apply -f api-deployment.yaml
kubectl apply -f celery-worker-deployment.yaml
kubectl apply -f celery-beat-deployment.yaml
kubectl apply -f api-service.yaml
kubectl apply -f api-ingress.yaml
```

Or apply all at once:

```bash
kubectl apply -f .
```

### 3. Verify Deployment

```bash
# Check all resources
kubectl get all -n ephemera-system

# Check ingress and certificates
kubectl get ingress,certificate -n ephemera-system

# Check logs
kubectl logs -n ephemera-system deployment/ephemera-api
kubectl logs -n ephemera-system deployment/ephemera-celery-worker
```

### 4. Update DNS

Add an A record in Cloudflare:
- **Type**: A
- **Name**: `ephemera-api`
- **Content**: `34.28.9.111` (your LoadBalancer IP)
- **Proxy**: DNS only (gray cloud)

### 5. Update GitHub Webhook

Update your GitHub App webhook URL to:
```
https://ephemera-api.devpreview.app/webhooks/github
```

## Architecture

```
Internet
    ↓
nginx-ingress (ephemera-api.devpreview.app)
    ↓
ephemera-api Service
    ↓
ephemera-api Pods (2 replicas)

ephemera-celery-worker Pods (2 replicas)
    ↓
Redis (AWS ElastiCache)

ephemera-celery-beat Pod (1 replica)
    ↓
Redis (AWS ElastiCache)

All pods connect to:
- PostgreSQL (AWS RDS)
- Redis (AWS ElastiCache)
- Kubernetes API (in-cluster)
```

## Scaling

```bash
# Scale API
kubectl scale deployment/ephemera-api -n ephemera-system --replicas=3

# Scale workers
kubectl scale deployment/ephemera-celery-worker -n ephemera-system --replicas=4
```

## Updating

When you push new code, GitHub Actions will rebuild images and update deployments automatically.

## Troubleshooting

### Check if pods are running
```bash
kubectl get pods -n ephemera-system
```

### Check pod logs
```bash
kubectl logs -n ephemera-system -l component=api --tail=100
kubectl logs -n ephemera-system -l component=celery-worker --tail=100
```

### Check secrets are loaded
```bash
kubectl exec -n ephemera-system deployment/ephemera-api -- env | grep DATABASE_URL
```

### Restart deployments
```bash
kubectl rollout restart deployment/ephemera-api -n ephemera-system
kubectl rollout restart deployment/ephemera-celery-worker -n ephemera-system
```
