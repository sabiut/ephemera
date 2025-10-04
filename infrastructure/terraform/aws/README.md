# Ephemera AWS Infrastructure

This directory contains Terraform configuration for deploying Ephemera to AWS.

## Quick Start

### 1. Prerequisites

```bash
# Install Terraform
brew install terraform  # macOS
# or download from https://www.terraform.io/downloads

# Install AWS CLI
brew install awscli

# Configure AWS credentials
aws configure
```

### 2. Configure Variables

```bash
# Copy example variables
cp terraform.tfvars.example terraform.tfvars

# Edit with your values
vim terraform.tfvars
```

### 3. Deploy Infrastructure

```bash
# Initialize Terraform
terraform init

# Review what will be created
terraform plan

# Create infrastructure (~15-20 minutes)
terraform apply

# Type 'yes' when prompted
```

### 4. Get Outputs

```bash
# Get all outputs
terraform output

# Get kubeconfig command
terraform output kubeconfig_command

# Get database password
terraform output get_database_password_command
```

### 5. Configure kubectl

```bash
# Update kubeconfig
aws eks update-kubeconfig --region us-west-2 --name ephemera-dev

# Verify connection
kubectl get nodes
```

## Cost Optimization

Current configuration is optimized for testing:
- **1x t3.small spot instance**: ~$0.007/hr
- **db.t3.micro**: FREE (with AWS free tier)
- **cache.t3.micro**: $0.017/hr
- **EKS cluster**: $0.10/hr
- **NAT Gateway**: $0.045/hr

**Total: ~$0.17/hr or $4/day**

### For 4-hour test: ~$0.68

## Cleanup

**IMPORTANT:** To avoid charges, destroy all resources when done testing:

```bash
terraform destroy

# Type 'yes' when prompted
```

## Architecture

```
┌─────────────────────────────────────┐
│          VPC (10.0.0.0/16)         │
├─────────────────────────────────────┤
│  ┌─────────────┐  ┌──────────────┐ │
│  │   Public    │  │   Private    │ │
│  │  Subnets    │  │   Subnets    │ │
│  │  (NAT GW)   │  │  (EKS Nodes) │ │
│  └─────────────┘  └──────────────┘ │
│                                     │
│  ┌────────────────────────────────┐│
│  │    EKS Cluster                 ││
│  │    - Control Plane             ││
│  │    - Worker Nodes (t3.small)   ││
│  └────────────────────────────────┘│
│                                     │
│  ┌────────────────────────────────┐│
│  │    RDS PostgreSQL (t3.micro)   ││
│  └────────────────────────────────┘│
│                                     │
│  ┌────────────────────────────────┐│
│  │    ElastiCache Redis (t3.micro)││
│  └────────────────────────────────┘│
└─────────────────────────────────────┘
```

## Modules

- **vpc**: VPC, subnets, NAT gateway, route tables
- **security**: Security groups for RDS and Redis
- **eks**: EKS cluster and node groups
- **rds**: PostgreSQL database with backups
- **elasticache**: Redis cluster with auth

## Troubleshooting

### Issue: Terraform init fails
```bash
# Remove lock file
rm .terraform.lock.hcl

# Re-initialize
terraform init
```

### Issue: Apply fails with capacity error
```bash
# Try different availability zone
# Edit terraform.tfvars and add:
# azs = ["us-west-2a", "us-west-2b", "us-west-2c"]
```

### Issue: Can't connect to cluster
```bash
# Update kubeconfig
aws eks update-kubeconfig --region us-west-2 --name ephemera-dev --profile your-profile

# Verify AWS credentials
aws sts get-caller-identity
```

## Next Steps

After infrastructure is created:
1. Deploy Ephemera application
2. Configure ingress controller
3. Set up DNS
4. Test webhook integration
