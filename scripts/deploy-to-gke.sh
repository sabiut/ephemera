#!/bin/bash
set -e

# Ephemera Manual Deployment Script
# This script builds and deploys Ephemera to GKE manually
# Useful for testing before using GitHub Actions

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
GCP_PROJECT_ID="ephemera-dev-2025"
GKE_CLUSTER="ephemera-dev"
GKE_REGION="us-central1"
GCR_REGISTRY="gcr.io/ephemera-dev-2025"
NAMESPACE="ephemera-system"

echo -e "${GREEN}=== Ephemera GKE Deployment ===${NC}\n"

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"
command -v docker >/dev/null 2>&1 || { echo -e "${RED}Error: docker is not installed${NC}" >&2; exit 1; }
command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}Error: kubectl is not installed${NC}" >&2; exit 1; }
command -v gcloud >/dev/null 2>&1 || { echo -e "${RED}Error: gcloud is not installed${NC}" >&2; exit 1; }

# Get GKE credentials
echo -e "\n${YELLOW}Getting GKE credentials...${NC}"
gcloud container clusters get-credentials $GKE_CLUSTER \
  --region $GKE_REGION \
  --project $GCP_PROJECT_ID

# Configure Docker for GCR
echo -e "\n${YELLOW}Configuring Docker for GCR...${NC}"
gcloud auth configure-docker

# Build and push images
BUILD_TAG=$(git rev-parse --short HEAD)
echo -e "\n${YELLOW}Building images with tag: ${BUILD_TAG}${NC}"

echo -e "\n${GREEN}Building API image...${NC}"
docker build -f api/Dockerfile \
  -t $GCR_REGISTRY/ephemera-api:$BUILD_TAG \
  -t $GCR_REGISTRY/ephemera-api:latest \
  ./api

echo -e "\n${GREEN}Pushing API image...${NC}"
docker push $GCR_REGISTRY/ephemera-api:$BUILD_TAG
docker push $GCR_REGISTRY/ephemera-api:latest

echo -e "\n${GREEN}Building Celery Worker image...${NC}"
docker build -f api/Dockerfile \
  -t $GCR_REGISTRY/ephemera-celery-worker:$BUILD_TAG \
  -t $GCR_REGISTRY/ephemera-celery-worker:latest \
  ./api

echo -e "\n${GREEN}Pushing Celery Worker image...${NC}"
docker push $GCR_REGISTRY/ephemera-celery-worker:$BUILD_TAG
docker push $GCR_REGISTRY/ephemera-celery-worker:latest

echo -e "\n${GREEN}Building Celery Beat image...${NC}"
docker build -f api/Dockerfile \
  -t $GCR_REGISTRY/ephemera-celery-beat:$BUILD_TAG \
  -t $GCR_REGISTRY/ephemera-celery-beat:latest \
  ./api

echo -e "\n${GREEN}Pushing Celery Beat image...${NC}"
docker push $GCR_REGISTRY/ephemera-celery-beat:$BUILD_TAG
docker push $GCR_REGISTRY/ephemera-celery-beat:latest

# Apply Kubernetes manifests
echo -e "\n${YELLOW}Applying Kubernetes manifests...${NC}"
kubectl apply -f infrastructure/k8s/ephemera/namespace.yaml
kubectl apply -f infrastructure/k8s/ephemera/configmap.yaml
kubectl apply -f infrastructure/k8s/ephemera/secret.yaml
kubectl apply -f infrastructure/k8s/ephemera/api-deployment.yaml
kubectl apply -f infrastructure/k8s/ephemera/celery-worker-deployment.yaml
kubectl apply -f infrastructure/k8s/ephemera/celery-beat-deployment.yaml
kubectl apply -f infrastructure/k8s/ephemera/api-service.yaml
kubectl apply -f infrastructure/k8s/ephemera/api-ingress.yaml

# Update deployment images
echo -e "\n${YELLOW}Updating deployment images to $BUILD_TAG...${NC}"
kubectl set image deployment/ephemera-api api=$GCR_REGISTRY/ephemera-api:$BUILD_TAG -n $NAMESPACE
kubectl set image deployment/ephemera-celery-worker celery-worker=$GCR_REGISTRY/ephemera-celery-worker:$BUILD_TAG -n $NAMESPACE
kubectl set image deployment/ephemera-celery-beat celery-beat=$GCR_REGISTRY/ephemera-celery-beat:$BUILD_TAG -n $NAMESPACE

# Wait for rollout
echo -e "\n${YELLOW}Waiting for rollout to complete...${NC}"
kubectl rollout status deployment/ephemera-api -n $NAMESPACE --timeout=5m
kubectl rollout status deployment/ephemera-celery-worker -n $NAMESPACE --timeout=5m
kubectl rollout status deployment/ephemera-celery-beat -n $NAMESPACE --timeout=5m

# Run migrations
echo -e "\n${YELLOW}Running database migrations...${NC}"
kubectl exec -n $NAMESPACE deployment/ephemera-api -- alembic upgrade head

# Show status
echo -e "\n${GREEN}=== Deployment Complete! ===${NC}\n"
echo -e "${YELLOW}Pods:${NC}"
kubectl get pods -n $NAMESPACE

echo -e "\n${YELLOW}Services:${NC}"
kubectl get svc -n $NAMESPACE

echo -e "\n${YELLOW}Ingress:${NC}"
kubectl get ingress -n $NAMESPACE

echo -e "\n${GREEN}API should be accessible at: https://ephemera-api.devpreview.app${NC}"
echo -e "${GREEN}Health check: https://ephemera-api.devpreview.app/health${NC}\n"
