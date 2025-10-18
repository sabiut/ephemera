# Multi-Cloud Architecture Strategy

Ephemera is designed from the ground up to support multiple cloud providers (GCP, AWS, Azure) while maintaining a unified deployment experience.

## Current Architecture Highlights

### 1. Cloud-Agnostic Core Services

The core application services are **completely cloud-agnostic**:

- **KubernetesService** ([api/app/services/kubernetes.py](api/app/services/kubernetes.py))
  - Uses standard Kubernetes API (works on GKE, EKS, AKS)
  - Supports both in-cluster config (production) and kubeconfig (development)
  - No cloud-specific dependencies

- **DeploymentService** ([api/app/services/deployment.py](api/app/services/deployment.py))
  - Converts docker-compose.yml to standard Kubernetes manifests
  - Generates standard Ingress resources (nginx-ingress works on all clouds)
  - Cloud-agnostic manifest generation

- **GitHubService** ([api/app/services/github.py](api/app/services/github.py))
  - Cloud-independent GitHub API integration
  - Works with any cloud provider

### 2. Separation of Infrastructure and Application

```
ephemera/
├── infrastructure/          # Cloud-specific infrastructure
│   └── terraform/
│       ├── aws/            # AWS-specific (EKS, RDS, ElastiCache)
│       ├── gcp/            # GCP-specific (GKE, Cloud SQL, Memorystore)
│       └── azure/          # Future: AKS, Azure Database, Azure Cache
│
└── api/                    # Cloud-agnostic application code
    └── app/
        └── services/       # All services use Kubernetes API
```

### 3. Standard Kubernetes Patterns

All deployments use **standard Kubernetes resources** that work across clouds:

- **Deployments**: Standard across GKE/EKS/AKS
- **Services**: Cloud-agnostic (ClusterIP, LoadBalancer)
- **Ingress**: nginx-ingress-controller (works on all platforms)
- **cert-manager**: Let's Encrypt TLS (cloud-independent)
- **Namespaces**: Standard Kubernetes isolation

### 4. Hybrid Cloud Support

The current deployment demonstrates **hybrid cloud** capability:

```
Current Production Setup (Hybrid):
┌─────────────────────────────────────────────────────────┐
│ GCP (Compute)                                           │
│ ├── GKE Cluster (ephemera-dev-2025-gke)               │
│ │   ├── Ephemera API (gcr.io/ephemera-dev-2025/...)   │
│ │   ├── Celery Workers                                │
│ │   └── Preview Environments (dynamic namespaces)     │
│ └── nginx-ingress + cert-manager                       │
└─────────────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────────┐
│ AWS (Data Layer)                                        │
│ ├── RDS PostgreSQL (database)                          │
│ └── ElastiCache Redis (celery broker + cache)          │
└─────────────────────────────────────────────────────────┘
```

This proves the architecture can **mix and match** cloud providers!

## Multi-Cloud Deployment Scenarios

### Scenario 1: Pure GCP
```yaml
Compute:  GKE
Database: Cloud SQL (PostgreSQL)
Cache:    Memorystore (Redis)
Registry: Google Container Registry (GCR)
DNS:      Cloud DNS or Cloudflare
```

### Scenario 2: Pure AWS
```yaml
Compute:  EKS
Database: RDS PostgreSQL
Cache:    ElastiCache Redis
Registry: Elastic Container Registry (ECR)
DNS:      Route 53 or Cloudflare
```

### Scenario 3: Pure Azure
```yaml
Compute:  AKS
Database: Azure Database for PostgreSQL
Cache:    Azure Cache for Redis
Registry: Azure Container Registry (ACR)
DNS:      Azure DNS or Cloudflare
```

### Scenario 4: Hybrid (Current)
```yaml
Compute:  GKE (GCP)
Database: RDS (AWS)
Cache:    ElastiCache (AWS)
Registry: GCR (GCP)
DNS:      Cloudflare
```

## Future Multi-Cloud Roadmap

### Phase 1: AWS Support (Next Priority)

**Infrastructure:**
```bash
infrastructure/terraform/aws/
├── main.tf                  # EKS cluster setup
├── modules/
│   ├── eks/                 # Amazon EKS
│   ├── rds/                 # Already exists
│   ├── elasticache/         # Already exists
│   ├── vpc/                 # Network setup
│   └── ecr/                 # Container registry
└── README.md
```

**Deployment:**
```bash
infrastructure/k8s/ephemera-aws/
├── aws-specific-configmap.yaml    # AWS_REGION, etc.
├── api-deployment.yaml            # Uses ECR images
└── README.md
```

**CI/CD:**
```yaml
.github/workflows/deploy-aws.yml
- Build and push to ECR
- Deploy to EKS
- Use AWS IAM roles for service accounts
```

### Phase 2: Azure Support

**Infrastructure:**
```bash
infrastructure/terraform/azure/
├── main.tf                  # AKS cluster setup
├── modules/
│   ├── aks/                 # Azure Kubernetes Service
│   ├── postgres/            # Azure Database for PostgreSQL
│   ├── redis/               # Azure Cache for Redis
│   ├── vnet/                # Virtual Network
│   └── acr/                 # Azure Container Registry
└── README.md
```

**Deployment:**
```bash
infrastructure/k8s/ephemera-azure/
├── azure-specific-configmap.yaml  # AZURE_REGION, etc.
├── api-deployment.yaml            # Uses ACR images
└── README.md
```

**CI/CD:**
```yaml
.github/workflows/deploy-azure.yml
- Build and push to ACR
- Deploy to AKS
- Use Azure managed identities
```

### Phase 3: Multi-Cloud Control Plane

Add cloud provider selection to the Ephemera API:

```python
# api/app/models/environment.py
class Environment(Base):
    cloud_provider = Column(String, default="gcp")  # gcp, aws, azure
    cluster_name = Column(String)                   # Which cluster to deploy to
```

```python
# api/app/services/cloud_provider.py
class CloudProviderFactory:
    @staticmethod
    def get_kubernetes_service(provider: str):
        if provider == "gcp":
            return GKEService()
        elif provider == "aws":
            return EKSService()
        elif provider == "azure":
            return AKSService()
```

## Key Design Principles

### 1. **Kubernetes as the Abstraction Layer**
- All services use Kubernetes API
- No direct cloud provider API calls in application code
- Cloud-specific logic only in Terraform infrastructure

### 2. **Standard Ingress Pattern**
- nginx-ingress-controller works on all clouds
- cert-manager for TLS (cloud-independent)
- Consistent URL patterns across providers

### 3. **Environment Variables for Configuration**
- All cloud-specific config in ConfigMaps/Secrets
- No hardcoded cloud provider logic
- Easy to switch providers by changing environment variables

### 4. **Terraform Modules for Infrastructure**
- Each cloud has its own Terraform directory
- Consistent module structure across providers
- Reusable patterns

### 5. **Container Registry Flexibility**
- GCR for GCP deployments
- ECR for AWS deployments
- ACR for Azure deployments
- All use standard Docker image format

## Configuration Strategy

### Cloud-Agnostic Configuration
```yaml
# infrastructure/k8s/ephemera/configmap.yaml (Current)
BASE_DOMAIN: "devpreview.app"
ENVIRONMENT: "production"
KUBERNETES_ENABLED: "true"
```

### Cloud-Specific Configuration (Future)
```yaml
# infrastructure/k8s/ephemera-aws/configmap.yaml
CLOUD_PROVIDER: "aws"
AWS_REGION: "us-east-1"
CLUSTER_NAME: "ephemera-prod-eks"
CONTAINER_REGISTRY: "123456789.dkr.ecr.us-east-1.amazonaws.com"
```

```yaml
# infrastructure/k8s/ephemera-azure/configmap.yaml
CLOUD_PROVIDER: "azure"
AZURE_REGION: "eastus"
CLUSTER_NAME: "ephemera-prod-aks"
CONTAINER_REGISTRY: "ephemera.azurecr.io"
```

## Benefits of This Architecture

### 1. **Vendor Independence**
- Not locked into any single cloud provider
- Can migrate between clouds with minimal code changes
- Negotiating power with cloud providers

### 2. **Geographic Distribution**
- Deploy to AWS in US-East
- Deploy to GCP in Europe
- Deploy to Azure in Asia
- Serve users from closest region

### 3. **Cost Optimization**
- Use cheapest provider for each service
- Spot instances on AWS, Preemptible VMs on GCP
- Mix and match based on pricing

### 4. **Disaster Recovery**
- Multi-cloud failover
- If GCP goes down, failover to AWS
- True high availability

### 5. **Customer Requirements**
- Enterprise customers may require specific clouds
- Government contracts (AWS GovCloud, Azure Government)
- Compliance requirements (HIPAA, SOC2 on specific providers)

## Implementation Timeline

### Current State (October 2024)
- ✅ GCP deployment ready
- ✅ Hybrid cloud proven (GCP + AWS)
- ✅ Cloud-agnostic application code

### Q4 2024 - Q1 2025
- AWS EKS full deployment
- Pure AWS infrastructure option
- AWS-specific CI/CD pipeline

### Q2 2025
- Azure AKS deployment
- Pure Azure infrastructure option
- Azure-specific CI/CD pipeline

### Q3 2025
- Multi-cloud control plane
- Cloud provider selection API
- Cross-cloud environment creation

## Testing Strategy

### Per-Cloud Testing
```bash
# Test on GCP
./scripts/test-gcp-deployment.sh

# Test on AWS
./scripts/test-aws-deployment.sh

# Test on Azure
./scripts/test-azure-deployment.sh
```

### Multi-Cloud Integration Tests
```bash
# Deploy to multiple clouds simultaneously
./scripts/test-multi-cloud.sh
```

## Documentation

Each cloud provider will have dedicated documentation:

```
docs/
├── deployment/
│   ├── gcp.md              # GCP/GKE deployment guide
│   ├── aws.md              # AWS/EKS deployment guide
│   ├── azure.md            # Azure/AKS deployment guide
│   └── hybrid.md           # Hybrid cloud setups
└── architecture/
    └── multi-cloud.md      # This document
```

## Conclusion

Ephemera's architecture is **already multi-cloud ready**:

1. ✅ **Cloud-agnostic core** (Kubernetes API)
2. ✅ **Separated infrastructure** (Terraform per cloud)
3. ✅ **Standard patterns** (Ingress, cert-manager)
4. ✅ **Hybrid cloud proven** (GCP + AWS working)

The foundation is solid. Adding AWS and Azure support is primarily an **infrastructure** task (Terraform + CI/CD), not an application code rewrite.

The next deployment (GCP) will serve as the reference implementation for future cloud providers.
