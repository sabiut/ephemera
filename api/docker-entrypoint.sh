#!/bin/bash
set -e

# Activate service account if key file exists
if [ -f "/tmp/gke-service-account-key.json" ]; then
    echo "Activating GCP service account..."
    gcloud auth activate-service-account --key-file=/tmp/gke-service-account-key.json
    echo "Service account activated successfully"
fi

# Execute the command passed to the container
exec "$@"
