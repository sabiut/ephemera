# Kubernetes Deployments

This directory contains Kubernetes manifests for deploying both Ephemera itself and the infrastructure components.

## Directory Structure

```
infrastructure/k8s/
├── README.md                    # This file
│
├── ephemera/                    # GCP/GKE deployment (READY)
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── secret.yaml (gitignored)
│   ├── api-deployment.yaml
│   ├── celery-worker-deployment.yaml
│   ├── celery-beat-deployment.yaml
│   ├── api-service.yaml
│   ├── api-ingress.yaml
│   └── README.md
│
├── ephemera-aws/                # AWS/EKS deployment (PLANNED)
│   └── README.md
│
├── ephemera-azure/              # Azure/AKS deployment (PLANNED)
│   └── README.md
│
├── letsencrypt-issuer.yaml      # Let's Encrypt ClusterIssuer
├── nginx-ingress-values.yaml    # nginx-ingress Helm values
└── cert-manager-values.yaml     # cert-manager Helm values
```

See [MULTI_CLOUD_ARCHITECTURE.md](../../MULTI_CLOUD_ARCHITECTURE.md) for the complete multi-cloud strategy.

## Let's Encrypt Certificate Issuers

See [LETSENCRYPT_SETUP.md](LETSENCRYPT_SETUP.md) for detailed setup instructions.

### Quick Setup

```bash
# Generate and apply ClusterIssuer with your email from .env
LETSENCRYPT_EMAIL=$(grep LETSENCRYPT_EMAIL api/.env | cut -d'=' -f2) \
  envsubst < infrastructure/k8s/letsencrypt-issuer.yaml.template \
  | kubectl apply -f -
```

### Security Note

- Email is stored in `api/.env` (gitignored)
- Template file uses `${LETSENCRYPT_EMAIL}` placeholder
- No personal information committed to repository
