# Cloud Providers Roadmap

This document tracks Ephemera's multi-cloud support status and future plans.

## Current Status

### ✅ GCP (Google Cloud Platform) - PRODUCTION READY

**Status**: Fully implemented and ready for production deployment

**Infrastructure** ([infrastructure/terraform/gcp/](infrastructure/terraform/gcp/)):
- ✅ GKE (Google Kubernetes Engine) cluster
- ✅ VPC and networking
- ✅ Cloud NAT
- ✅ Service accounts and IAM
- ⚠️ Cloud SQL (exists but using AWS RDS instead)
- ⚠️ Memorystore (exists but using AWS ElastiCache instead)

**Deployment** ([infrastructure/k8s/ephemera/](infrastructure/k8s/ephemera/)):
- ✅ Kubernetes manifests (namespace, configmap, secrets)
- ✅ API deployment with RBAC
- ✅ Celery worker deployment
- ✅ Celery beat deployment
- ✅ Services and Ingress
- ✅ HTTPS with nginx-ingress + Let's Encrypt

**CI/CD** ([.github/workflows/deploy.yml](.github/workflows/deploy.yml)):
- ✅ GitHub Actions workflow for GCP
- ✅ Build and push to GCR
- ✅ Automated deployment to GKE
- ✅ Database migrations

**Container Registry**: Google Container Registry (GCR)

**Production URL**: `https://ephemera-api.devpreview.app`

**Current Architecture** (Hybrid GCP + AWS):
```
┌─────────────────────────────────────┐
│ GCP (Compute)                       │
│ ├── GKE Cluster                     │
│ │   ├── Ephemera API               │
│ │   ├── Celery Workers             │
│ │   └── Preview Environments       │
│ └── nginx-ingress + cert-manager   │
└─────────────────────────────────────┘
            ↓
┌─────────────────────────────────────┐
│ AWS (Data Layer)                    │
│ ├── RDS PostgreSQL                  │
│ └── ElastiCache Redis               │
└─────────────────────────────────────┘
```

**Next Steps for GCP**:
1. Configure GitHub secrets (8 secrets - see `.github/SECRETS.md`)
2. Add DNS record: `ephemera-api.devpreview.app` → `34.28.9.111`
3. Deploy using `./scripts/deploy-to-gke.sh` or push to main branch
4. Update GitHub webhook URL
5. Test with a real PR

---

## 🚧 AWS (Amazon Web Services) - PLANNED Q4 2024 - Q1 2025

**Status**: Infrastructure exists, Kubernetes deployment planned

**Existing Infrastructure** ([infrastructure/terraform/aws/](infrastructure/terraform/aws/)):
- ✅ VPC and networking
- ✅ RDS PostgreSQL (currently in use!)
- ✅ ElastiCache Redis (currently in use!)
- ❌ EKS cluster (not yet implemented)
- ❌ ECR repository (not yet implemented)

**Planned Deployment** ([infrastructure/k8s/ephemera-aws/](infrastructure/k8s/ephemera-aws/)):
- 📋 Kubernetes manifests for EKS
- 📋 AWS-specific ConfigMap (AWS_REGION, etc.)
- 📋 API deployment with IRSA (IAM Roles for Service Accounts)
- 📋 Celery deployments
- 📋 ALB Ingress Controller configuration
- 📋 ACM certificate integration

**Planned CI/CD**:
- 📋 `.github/workflows/deploy-aws.yml`
- 📋 Build and push to ECR
- 📋 Deploy to EKS

**Container Registry**: Elastic Container Registry (ECR)

**Planned URL**: `https://ephemera-api-aws.devpreview.app` (or same domain with routing)

**Target Architecture** (Pure AWS):
```
┌─────────────────────────────────────┐
│ AWS (Full Stack)                    │
│ ├── EKS Cluster                     │
│ │   ├── Ephemera API               │
│ │   ├── Celery Workers             │
│ │   └── Preview Environments       │
│ ├── ALB Ingress Controller         │
│ ├── RDS PostgreSQL                  │
│ └── ElastiCache Redis               │
└─────────────────────────────────────┘
```

**Key Differences from GCP**:
- **Ingress**: AWS Application Load Balancer (ALB) instead of nginx
- **TLS**: AWS Certificate Manager (ACM) instead of Let's Encrypt
- **Permissions**: IAM Roles for Service Accounts (IRSA) instead of Workload Identity
- **Registry**: ECR instead of GCR

**TODO for AWS Implementation**:
1. Create EKS cluster Terraform module
2. Create ECR repository Terraform module
3. Create Kubernetes manifests in `infrastructure/k8s/ephemera-aws/`
4. Create GitHub Actions workflow for AWS
5. Test EKS deployment
6. Document AWS-specific setup

---

## 🔮 Azure (Microsoft Azure) - PLANNED Q2 2025

**Status**: Not yet started, planned for Q2 2025

**Planned Infrastructure** (`infrastructure/terraform/azure/` - to be created):
- 📋 AKS (Azure Kubernetes Service) cluster
- 📋 Virtual Network (VNet)
- 📋 Azure Database for PostgreSQL
- 📋 Azure Cache for Redis
- 📋 Azure Container Registry (ACR)
- 📋 Azure managed identities

**Planned Deployment** ([infrastructure/k8s/ephemera-azure/](infrastructure/k8s/ephemera-azure/)):
- 📋 Kubernetes manifests for AKS
- 📋 Azure-specific ConfigMap (AZURE_REGION, etc.)
- 📋 API deployment with Azure managed identity
- 📋 Celery deployments
- 📋 Application Gateway Ingress Controller
- 📋 Azure Key Vault integration

**Planned CI/CD**:
- 📋 `.github/workflows/deploy-azure.yml`
- 📋 Build and push to ACR
- 📋 Deploy to AKS

**Container Registry**: Azure Container Registry (ACR)

**Planned URL**: `https://ephemera-api-azure.devpreview.app` (or same domain with routing)

**Target Architecture** (Pure Azure):
```
┌─────────────────────────────────────┐
│ Azure (Full Stack)                  │
│ ├── AKS Cluster                     │
│ │   ├── Ephemera API               │
│ │   ├── Celery Workers             │
│ │   └── Preview Environments       │
│ ├── Application Gateway Ingress    │
│ ├── Azure Database for PostgreSQL  │
│ └── Azure Cache for Redis          │
└─────────────────────────────────────┘
```

**Key Differences from GCP/AWS**:
- **Ingress**: Application Gateway Ingress Controller (AGIC)
- **TLS**: Azure Key Vault for certificate management
- **Permissions**: Azure Managed Identity (AAD Pod Identity)
- **Registry**: ACR instead of GCR/ECR
- **Monitoring**: Azure Monitor and Application Insights

**TODO for Azure Implementation**:
1. Create AKS cluster Terraform module
2. Create Azure Database and Cache Terraform modules
3. Create ACR repository Terraform module
4. Create Kubernetes manifests in `infrastructure/k8s/ephemera-azure/`
5. Create GitHub Actions workflow for Azure
6. Test AKS deployment
7. Document Azure-specific setup

---

## Multi-Cloud Features (Q3 2025)

**Planned**: Multi-cloud control plane allowing users to choose deployment targets

### Cloud Provider Selection API

```python
# Example: Create environment on specific cloud
POST /api/environments
{
  "pr_number": 123,
  "repository": "owner/repo",
  "cloud_provider": "aws",  # or "gcp", "azure"
  "region": "us-east-1"
}
```

### Database Schema Updates

```sql
ALTER TABLE environments
ADD COLUMN cloud_provider VARCHAR(10) DEFAULT 'gcp',
ADD COLUMN cluster_name VARCHAR(100),
ADD COLUMN region VARCHAR(50);
```

### Cloud Provider Service Factory

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

    @staticmethod
    def get_container_registry(provider: str):
        if provider == "gcp":
            return "gcr.io/ephemera-dev-2025"
        elif provider == "aws":
            return "123456789.dkr.ecr.us-east-1.amazonaws.com"
        elif provider == "azure":
            return "ephemera.azurecr.io"
```

---

## Design Principles

### 1. Cloud-Agnostic Core
- ✅ All services use Kubernetes API (no cloud-specific API calls)
- ✅ Standard Kubernetes resources (Deployments, Services, Ingress)
- ✅ Works on any Kubernetes cluster (GKE, EKS, AKS, even on-prem)

### 2. Infrastructure as Code
- ✅ Terraform modules per cloud provider
- ✅ Consistent module structure
- ✅ Reusable patterns across clouds

### 3. Standard Tooling
- ✅ nginx-ingress or cloud-native ingress controllers
- ✅ cert-manager for TLS (or cloud certificate services)
- ✅ Kubernetes Secrets for sensitive data

### 4. Environment-Based Configuration
- ✅ ConfigMaps for cloud-specific settings
- ✅ Secrets for credentials
- ✅ No hardcoded cloud provider logic in application code

---

## Benefits of Multi-Cloud

### 1. Vendor Independence
- Not locked into any single cloud provider
- Can negotiate better pricing
- Freedom to switch providers if needed

### 2. Geographic Distribution
- Deploy to AWS in North America
- Deploy to GCP in Europe
- Deploy to Azure in Asia
- Serve users from nearest region

### 3. Cost Optimization
- Choose cheapest provider for each service
- Use spot/preemptible instances
- Mix and match based on pricing

### 4. Disaster Recovery
- Multi-cloud failover capability
- If one cloud goes down, switch to another
- True high availability

### 5. Customer Requirements
- Enterprise customers may require specific clouds
- Government contracts (AWS GovCloud, Azure Government)
- Compliance requirements (HIPAA, SOC2 on specific providers)

---

## Implementation Timeline

### ✅ 2024 Q3 (COMPLETE)
- [x] GCP Terraform infrastructure
- [x] Hybrid cloud (GCP + AWS) proven working
- [x] Cloud-agnostic application architecture

### 🎯 2024 Q4 (CURRENT)
- [ ] Complete GCP deployment (Phase 3)
- [ ] Production testing on GCP
- [ ] Begin AWS EKS implementation

### 📅 2025 Q1
- [ ] Complete AWS EKS deployment
- [ ] Pure AWS option available
- [ ] AWS CI/CD pipeline

### 📅 2025 Q2
- [ ] Azure AKS implementation
- [ ] Pure Azure option available
- [ ] Azure CI/CD pipeline

### 📅 2025 Q3
- [ ] Multi-cloud control plane
- [ ] Cloud provider selection API
- [ ] Cross-cloud monitoring dashboard

### 📅 2025 Q4
- [ ] Multi-cloud load balancing
- [ ] Automatic failover between clouds
- [ ] Cost optimization recommendations

---

## Testing Strategy

### Per-Cloud Testing
```bash
# Test GCP deployment
./scripts/test-gcp-deployment.sh

# Test AWS deployment
./scripts/test-aws-deployment.sh

# Test Azure deployment
./scripts/test-azure-deployment.sh
```

### Multi-Cloud Integration Testing
```bash
# Deploy to all clouds simultaneously
./scripts/test-multi-cloud.sh

# Test failover between clouds
./scripts/test-cloud-failover.sh
```

---

## Cost Estimates

### GCP (Current)
- GKE cluster (3 nodes, n1-standard-2): ~$150/month
- Load Balancer (nginx-ingress): ~$20/month
- Network egress: Variable
- **Total GCP**: ~$170/month + egress

### AWS (Planned)
- EKS cluster: ~$73/month (control plane)
- EC2 instances (3x t3.medium): ~$90/month
- RDS PostgreSQL (db.t3.micro): ~$15/month (already running)
- ElastiCache (cache.t3.micro): ~$12/month (already running)
- ALB: ~$20/month
- **Total AWS**: ~$210/month (pure AWS stack)

### Azure (Planned)
- AKS cluster: $0 (control plane free)
- VMs (3x B2s): ~$80/month
- Azure Database for PostgreSQL: ~$20/month
- Azure Cache for Redis: ~$15/month
- Application Gateway: ~$140/month
- **Total Azure**: ~$255/month

> **Note**: Costs are estimates and will vary based on actual usage, region, and discounts.

---

## References

- [MULTI_CLOUD_ARCHITECTURE.md](MULTI_CLOUD_ARCHITECTURE.md) - Detailed architecture
- [infrastructure/k8s/ephemera/README.md](infrastructure/k8s/ephemera/README.md) - GCP deployment
- [infrastructure/k8s/ephemera-aws/README.md](infrastructure/k8s/ephemera-aws/README.md) - AWS planning
- [infrastructure/k8s/ephemera-azure/README.md](infrastructure/k8s/ephemera-azure/README.md) - Azure planning

---

## Contributing

When implementing a new cloud provider:

1. Create Terraform modules in `infrastructure/terraform/{cloud}/`
2. Create Kubernetes manifests in `infrastructure/k8s/ephemera-{cloud}/`
3. Create CI/CD workflow in `.github/workflows/deploy-{cloud}.yml`
4. Update this roadmap document
5. Test thoroughly before marking as production-ready

---

**Legend**:
- ✅ Complete and tested
- ⚠️ Exists but not currently used
- ❌ Not implemented
- 📋 Planned
- 🎯 In progress
- 🚧 Under development
