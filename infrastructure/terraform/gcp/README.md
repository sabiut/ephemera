# GCP Infrastructure - Ephemera

This directory contains Terraform configurations for deploying Ephemera infrastructure on Google Cloud Platform (GCP).

## Architecture

- **GKE (Google Kubernetes Engine)**: Container orchestration for preview environments
- **Cloud SQL**: Managed PostgreSQL database
- **Memorystore**: Managed Redis for caching and Celery broker
- **VPC**: Private network with Cloud NAT for outbound internet access
- **IAM**: Service accounts with Workload Identity for secure pod authentication

## Prerequisites

1. **GCP Account** with billing enabled
2. **gcloud CLI** installed and configured
3. **Terraform** >= 1.0
4. **kubectl** for Kubernetes management

## Cost Estimates

### Development Environment (Preemptible)
- **GKE Nodes** (1 x e2-small preemptible): ~$5/month
- **Cloud SQL** (db-f1-micro): FREE (free tier eligible)
- **Memorystore** (1GB BASIC tier): ~$26/month
- **Networking**: ~$5/month
- **Total**: ~$36/month

### 4-Hour Test
- **Total**: ~$0.17

### Production Environment (Non-preemptible, HA)
- **GKE Nodes** (3 x e2-medium): ~$90/month
- **Cloud SQL** (db-custom-2-7680 HA): ~$180/month
- **Memorystore** (5GB STANDARD_HA): ~$160/month
- **Networking**: ~$20/month
- **Total**: ~$450/month

## Quick Start

### 1. Authenticate with GCP

```bash
gcloud auth login
gcloud auth application-default login
```

### 2. Create GCP Project (if needed)

```bash
export PROJECT_ID="ephemera-$(date +%s)"
gcloud projects create $PROJECT_ID
gcloud config set project $PROJECT_ID

# Enable billing (replace BILLING_ACCOUNT_ID with your billing account)
gcloud billing projects link $PROJECT_ID --billing-account=BILLING_ACCOUNT_ID
```

### 3. Enable Required APIs

```bash
gcloud services enable compute.googleapis.com
gcloud services enable container.googleapis.com
gcloud services enable sqladmin.googleapis.com
gcloud services enable redis.googleapis.com
gcloud services enable servicenetworking.googleapis.com
gcloud services enable secretmanager.googleapis.com
```

### 4. Configure Terraform Variables

```bash
cd infrastructure/terraform/gcp
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:
```hcl
gcp_project_id = "your-gcp-project-id"
gcp_region     = "us-central1"
environment    = "dev"
owner          = "your-name"
```

### 5. Initialize Terraform

```bash
terraform init
```

### 6. Review Planned Changes

```bash
terraform plan
```

### 7. Deploy Infrastructure

```bash
terraform apply
```

Deployment takes approximately 15-20 minutes.

### 8. Configure kubectl

```bash
gcloud container clusters get-credentials ephemera-dev --region us-central1 --project your-gcp-project-id
```

Verify:
```bash
kubectl get nodes
```

### 9. Get Database Credentials

```bash
# Get password from Secret Manager
gcloud secrets versions access latest --secret="ephemera-dev-db-password"

# Get connection details
terraform output cloudsql_connection_name
terraform output cloudsql_private_ip
```

### 10. Update Application Configuration

Create `api/.env` with Terraform outputs:
```bash
# Get values from terraform output
terraform output -json > outputs.json

# Extract values (manually or with jq)
DATABASE_URL=postgresql://ephemera:<password>@<private_ip>:5432/ephemera
REDIS_URL=redis://<redis_host>:6379/0
```

## Resource Details

### VPC Network
- **CIDR**: 10.0.0.0/20 (4,096 IPs)
- **Pod CIDR**: 10.4.0.0/14 (262,144 IPs)
- **Service CIDR**: 10.8.0.0/20 (4,096 IPs)
- **Private Google Access**: Enabled
- **Cloud NAT**: Single NAT gateway for cost optimization

### GKE Cluster
- **Version**: 1.28 (auto-upgrade enabled)
- **Node Machine**: e2-small (2 vCPU, 2GB RAM)
- **Node Pool**: 1-3 nodes with autoscaling
- **Preemptible**: Enabled (80% cost savings)
- **Workload Identity**: Enabled for secure authentication
- **Private Nodes**: Nodes don't have public IPs

### Cloud SQL
- **Version**: PostgreSQL 15
- **Tier**: db-f1-micro (free tier eligible)
- **Storage**: 10GB SSD with auto-resize
- **Backups**: Daily at 02:00 UTC, 7-day retention
- **Private IP**: VPC-native, no public access
- **SSL**: Required

### Memorystore (Redis)
- **Version**: Redis 7.0
- **Tier**: BASIC (single node)
- **Memory**: 1GB
- **Auth**: Enabled
- **Encryption**: TLS in transit

## Cost Optimization

### For Testing
1. **Use Preemptible Nodes**: `use_preemptible_nodes = true` (80% savings)
2. **Minimum Node Count**: `node_min_count = 1`
3. **Small Instance Sizes**: `node_machine_type = "e2-small"`, `db_tier = "db-f1-micro"`
4. **BASIC Redis Tier**: `redis_tier = "BASIC"`
5. **Destroy When Not in Use**: `terraform destroy`

### For Production
1. **Use Regular Nodes**: `use_preemptible_nodes = false`
2. **High Availability**: `db_tier = "db-custom-2-7680"`, `redis_tier = "STANDARD_HA"`
3. **More Nodes**: `node_min_count = 3`
4. **Larger Instances**: `node_machine_type = "e2-medium"`
5. **Enable Point-in-Time Recovery** for database

## Billing Alerts

Set up billing alerts to avoid surprises:

```bash
# Create budget alert
gcloud billing budgets create \
  --billing-account=BILLING_ACCOUNT_ID \
  --display-name="Ephemera Dev Budget" \
  --budget-amount=50 \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90 \
  --threshold-rule=percent=100
```

## Cleanup

To destroy all infrastructure:

```bash
terraform destroy
```

**Warning**: This will permanently delete:
- GKE cluster and all deployments
- Cloud SQL database and all data
- Memorystore Redis instance
- VPC network and subnets

Make sure to backup any important data before destroying!

## Remote State (Recommended for Teams)

1. Create GCS bucket for state:
```bash
gsutil mb -p your-gcp-project-id gs://ephemera-terraform-state
gsutil versioning set on gs://ephemera-terraform-state
```

2. Uncomment backend configuration in `main.tf`:
```hcl
backend "gcs" {
  bucket = "ephemera-terraform-state"
  prefix = "gcp/terraform.tfstate"
}
```

3. Reinitialize Terraform:
```bash
terraform init -migrate-state
```

## Troubleshooting

### Error: API not enabled
```bash
gcloud services enable <api-name>.googleapis.com
```

### Error: Quota exceeded
Check quotas: https://console.cloud.google.com/iam-admin/quotas
Request quota increase if needed.

### Error: Insufficient permissions
Ensure your account has these roles:
- `roles/container.admin` (GKE)
- `roles/compute.admin` (Networking)
- `roles/cloudsql.admin` (Cloud SQL)
- `roles/redis.admin` (Memorystore)
- `roles/iam.serviceAccountAdmin` (IAM)

### GKE cluster not accessible
```bash
gcloud container clusters get-credentials ephemera-dev --region us-central1
```

### Database connection issues
- Verify private IP connectivity from GKE
- Check VPC peering: `gcloud services vpc-peerings list --network=ephemera-dev-vpc`
- Verify password from Secret Manager

## Security Best Practices

1. **Never commit secrets**: Use Secret Manager for sensitive data
2. **Use Workload Identity**: Avoid service account keys in pods
3. **Enable Private Nodes**: Nodes don't have public IPs
4. **Enable SSL/TLS**: All data encrypted in transit
5. **Regular Updates**: Auto-upgrade enabled for GKE
6. **Audit Logging**: Enabled by default in GCP

## Next Steps

1. Deploy Ephemera application to GKE
2. Set up Ingress controller (nginx or GKE Ingress)
3. Configure cert-manager for TLS certificates
4. Set up external-dns for automatic DNS records
5. Configure monitoring with Cloud Monitoring
6. Set up Cloud Logging for centralized logs

## Support

For issues specific to this Terraform configuration:
- Check [Terraform GCP Provider Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs)
- Review [GKE Documentation](https://cloud.google.com/kubernetes-engine/docs)
- See [Cloud SQL Best Practices](https://cloud.google.com/sql/docs/postgres/best-practices)

For Ephemera application issues:
- See main [README.md](../../../README.md)
- Check [GitHub Issues](https://github.com/sabiut/ephemera/issues)
