terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }

  # Uncomment to use S3 backend for remote state (recommended for team)
  # backend "s3" {
  #   bucket = "ephemera-terraform-state"
  #   key    = "aws/terraform.tfstate"
  #   region = "us-west-2"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

# Data sources
data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

# Local variables
locals {
  cluster_name = "eph-${var.environment}"

  common_tags = {
    Project     = "Ephemera"
    Environment = var.environment
    ManagedBy   = "Terraform"
    Owner       = var.owner
  }

  # Use first 3 AZs
  azs = slice(data.aws_availability_zones.available.names, 0, 3)
}

# VPC Module
module "vpc" {
  source = "./modules/vpc"

  cluster_name = local.cluster_name
  environment  = var.environment
  azs          = local.azs
  tags         = local.common_tags
}

# Security Groups Module
module "security" {
  source = "./modules/security"

  cluster_name = local.cluster_name
  vpc_id       = module.vpc.vpc_id
  environment  = var.environment
  tags         = local.common_tags
}

# EKS Module
module "eks" {
  source = "./modules/eks"

  cluster_name        = local.cluster_name
  cluster_version     = var.cluster_version
  environment         = var.environment
  vpc_id              = module.vpc.vpc_id
  private_subnet_ids  = module.vpc.private_subnet_ids
  node_instance_type  = var.node_instance_type
  node_desired_size   = var.node_desired_size
  node_min_size       = var.node_min_size
  node_max_size       = var.node_max_size
  use_spot_instances  = var.use_spot_instances
  tags                = local.common_tags
}

# RDS Module
module "rds" {
  source = "./modules/rds"

  cluster_name          = local.cluster_name
  environment           = var.environment
  vpc_id                = module.vpc.vpc_id
  private_subnet_ids    = module.vpc.private_subnet_ids
  security_group_id     = module.security.rds_security_group_id
  db_instance_class     = var.db_instance_class
  db_name               = var.db_name
  db_username           = var.db_username
  tags                  = local.common_tags
}

# ElastiCache Module
module "elasticache" {
  source = "./modules/elasticache"

  cluster_name       = local.cluster_name
  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  security_group_id  = module.security.redis_security_group_id
  redis_node_type    = var.redis_node_type
  tags               = local.common_tags
}

# Network Load Balancer Module for nginx-ingress
module "nlb" {
  source = "./modules/nlb"

  cluster_name       = local.cluster_name
  vpc_id             = module.vpc.vpc_id
  public_subnet_ids  = module.vpc.public_subnet_ids
  http_nodeport      = 30080
  https_nodeport     = 30443
  tags               = local.common_tags

  depends_on = [module.eks]
}
