# Terraform Destroy Order for GCP Infrastructure

This document explains the correct order to destroy GCP resources to avoid dependency issues.

## The Problem

Google Cloud has a dependency chain:
```
VPC Network
    ↓
VPC Peering (Service Networking Connection)
    ↓
Cloud SQL / Memorystore (depend on peering)
```

If you try to destroy everything with `terraform destroy`, the VPC peering cannot be deleted while Cloud SQL or Memorystore instances are still using it.

## Solution: Targeted Destroy

Use Terraform's `-target` flag to destroy resources in the correct order:

### Step 1: Destroy Cloud SQL First

```bash
terraform destroy -target=module.cloudsql -auto-approve
```

This destroys:
- Cloud SQL database instance
- Database users
- Secret Manager secrets for passwords
- Random password generators

### Step 2: Destroy Memorystore

```bash
terraform destroy -target=module.memorystore -auto-approve
```

This destroys:
- Redis instance

### Step 3: Destroy GKE Cluster (if deployed)

```bash
terraform destroy -target=module.gke -auto-approve
```

This destroys:
- GKE cluster
- Node pools
- Autopilot or standard cluster resources

### Step 4: Destroy IAM Resources

```bash
terraform destroy -target=module.iam -auto-approve
```

This destroys:
- Service accounts
- IAM role bindings

### Step 5: Destroy VPC (Last)

```bash
terraform destroy -target=module.vpc -auto-approve
```

This destroys:
- VPC peering connection (now safe to delete)
- Reserved IP address
- Cloud Router and Cloud NAT
- Firewall rules
- Subnets
- VPC network

### Step 6: Cleanup Remaining Resources

```bash
terraform destroy -auto-approve
```

This ensures any remaining resources are cleaned up.

## One-Command Alternative

If you want to destroy everything in one command, use this script:

```bash
#!/bin/bash
set -e

echo "=== Destroying GCP Infrastructure in Correct Order ==="

echo "Step 1/5: Destroying Cloud SQL..."
terraform destroy -target=module.cloudsql -auto-approve

echo "Step 2/5: Destroying Memorystore..."
terraform destroy -target=module.memorystore -auto-approve

echo "Step 3/5: Destroying GKE..."
terraform destroy -target=module.gke -auto-approve

echo "Step 4/5: Destroying IAM..."
terraform destroy -target=module.iam -auto-approve

echo "Step 5/5: Destroying VPC..."
terraform destroy -target=module.vpc -auto-approve

echo "=== Cleanup: Destroying any remaining resources ==="
terraform destroy -auto-approve

echo "=== All resources destroyed successfully! ==="
```

Save this as `destroy.sh` and run:
```bash
chmod +x destroy.sh
./destroy.sh
```

## Why This Order Matters

1. **Cloud SQL and Memorystore** have `depends_on = [var.private_vpc_connection]` in their Terraform config
2. This means Terraform knows they depend on the VPC peering
3. **BUT** Google Cloud also enforces this at the API level
4. If you try to delete the VPC peering while instances exist, Google Cloud returns error: "Producer services are still using this connection"
5. Destroying in reverse dependency order (data → peering → network) avoids this error

## Terraform Dependency Graph

The correct dependency flow is:

```
Create Order (terraform apply):
VPC → Peering → Cloud SQL/Memorystore → GKE

Destroy Order (terraform destroy):
GKE → Cloud SQL/Memorystore → Peering → VPC
```

## Verification

After destruction, verify all resources are gone:

```bash
# Check VPC networks
gcloud compute networks list --project=ephemera-dev-2025

# Check Cloud SQL
gcloud sql instances list --project=ephemera-dev-2025

# Check Redis/Memorystore
gcloud redis instances list --region=us-central1 --project=ephemera-dev-2025

# Check GKE clusters
gcloud container clusters list --project=ephemera-dev-2025
```

Only the `default` VPC network should remain (this is managed by Google and cannot be deleted).

## Cost Implications

Destroying resources in the wrong order can leave orphaned resources that continue to cost money:

- **VPC Peering**: Free, but blocks cleanup
- **Cloud SQL**: Costs per hour even if not used (~$15-100/month)
- **Memorystore**: Costs per hour (~$12-50/month)
- **GKE**: Costs per hour for nodes (~$150/month)

**Always verify all resources are destroyed to avoid unexpected costs!**

## Troubleshooting

### Error: "Failed to delete connection; Producer services are still using this connection"

This means Cloud SQL or Memorystore instances still exist. Run:

```bash
# List Cloud SQL instances
gcloud sql instances list --project=ephemera-dev-2025

# List Redis instances (check all regions)
gcloud redis instances list --region=us-central1 --project=ephemera-dev-2025

# Delete manually if they exist outside Terraform
gcloud sql instances delete INSTANCE_NAME --project=ephemera-dev-2025
gcloud redis instances delete INSTANCE_NAME --region=us-central1 --project=ephemera-dev-2025
```

### Error: "No Terraform state found"

If Terraform state is lost/deleted, you'll need to manually clean up GCP resources:

```bash
# Delete Cloud SQL
gcloud sql instances list --project=ephemera-dev-2025
gcloud sql instances delete INSTANCE_NAME --project=ephemera-dev-2025 --quiet

# Delete Memorystore
gcloud redis instances list --region=us-central1 --project=ephemera-dev-2025
gcloud redis instances delete INSTANCE_NAME --region=us-central1 --project=ephemera-dev-2025 --quiet

# Delete GKE
gcloud container clusters list --project=ephemera-dev-2025
gcloud container clusters delete CLUSTER_NAME --region=us-central1 --project=ephemera-dev-2025 --quiet

# Delete VPC network (cascade deletes peering, subnets, etc.)
gcloud compute networks delete ephemera-dev-vpc --project=ephemera-dev-2025 --quiet
```

## Best Practices

1. **Always use `terraform plan -destroy`** before running destroy to see what will be removed
2. **Consider using `-target`** for large infrastructures to control destroy order
3. **Keep Terraform state safe** - use remote state in GCS
4. **Monitor costs** - set up billing alerts in Google Cloud Console
5. **Document custom destroy procedures** for your team

## Related Files

- [modules/vpc/main.tf](modules/vpc/main.tf) - VPC and peering configuration
- [modules/cloudsql/main.tf](modules/cloudsql/main.tf) - Cloud SQL with `depends_on` peering
- [modules/memorystore/main.tf](modules/memorystore/main.tf) - Redis with `depends_on` peering
