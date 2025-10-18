#!/bin/bash
# Destroy GCP infrastructure in the correct order to avoid dependency errors

set -e

echo "=========================================="
echo "GCP Infrastructure Destruction Script"
echo "=========================================="
echo ""
echo "This script will destroy resources in the correct order:"
echo "1. Cloud SQL (database instances)"
echo "2. Memorystore (Redis instances)"
echo "3. GKE (Kubernetes cluster)"
echo "4. IAM (Service accounts)"
echo "5. VPC (Network, peering, subnets)"
echo ""
read -p "Are you sure you want to destroy ALL GCP infrastructure? (type 'yes' to confirm): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Destruction cancelled."
    exit 0
fi

echo ""
echo "=========================================="
echo "Step 1/5: Destroying Cloud SQL..."
echo "=========================================="
terraform destroy -target=module.cloudsql -auto-approve || echo "Cloud SQL module not found or already destroyed"

echo ""
echo "=========================================="
echo "Step 2/5: Destroying Memorystore..."
echo "=========================================="
terraform destroy -target=module.memorystore -auto-approve || echo "Memorystore module not found or already destroyed"

echo ""
echo "=========================================="
echo "Step 3/5: Destroying GKE Cluster..."
echo "=========================================="
terraform destroy -target=module.gke -auto-approve || echo "GKE module not found or already destroyed"

echo ""
echo "=========================================="
echo "Step 4/5: Destroying IAM Resources..."
echo "=========================================="
terraform destroy -target=module.iam -auto-approve || echo "IAM module not found or already destroyed"

echo ""
echo "=========================================="
echo "Step 5/5: Destroying VPC Network..."
echo "=========================================="
terraform destroy -target=module.vpc -auto-approve || echo "VPC module not found or already destroyed"

echo ""
echo "=========================================="
echo "Cleanup: Destroying remaining resources..."
echo "=========================================="
terraform destroy -auto-approve || echo "No remaining resources to destroy"

echo ""
echo "=========================================="
echo "Verifying destruction..."
echo "=========================================="

# Check for remaining resources
echo ""
echo "Remaining VPC networks:"
gcloud compute networks list --project=ephemera-dev-2025 --format="table(name)" 2>/dev/null || echo "Unable to check"

echo ""
echo "Remaining Cloud SQL instances:"
gcloud sql instances list --project=ephemera-dev-2025 --format="table(name)" 2>/dev/null || echo "Unable to check"

echo ""
echo "Remaining GKE clusters:"
gcloud container clusters list --project=ephemera-dev-2025 --format="table(name)" 2>/dev/null || echo "Unable to check"

echo ""
echo "=========================================="
echo "✅ Destruction complete!"
echo "=========================================="
echo ""
echo "Note: The 'default' VPC network is managed by Google and cannot be deleted."
echo "This is normal and does not incur costs."
