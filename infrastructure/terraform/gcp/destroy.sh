#!/bin/bash
set -e

echo "Preparing to destroy Ephemera infrastructure..."

# Remove database and user from state to avoid deletion errors
echo "Removing database and user from Terraform state..."
terraform state rm module.cloudsql.google_sql_database.database 2>/dev/null || true
terraform state rm module.cloudsql.google_sql_user.user 2>/dev/null || true

echo "Running terraform destroy..."
terraform destroy "$@"

echo "Infrastructure destroyed successfully!"
