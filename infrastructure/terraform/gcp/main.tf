terraform {
  required_version = ">= 1.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
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

  # GCS backend for remote state (allows GitHub Actions to access state)
  backend "gcs" {
    bucket = "ephemera-terraform-state-2025"
    prefix = "gcp/terraform.tfstate"
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

provider "google-beta" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

provider "kubernetes" {
  host                   = "https://${module.gke.cluster_endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(module.gke.cluster_ca_certificate)
}

# Data sources
data "google_client_config" "default" {}

data "google_project" "project" {
  project_id = var.gcp_project_id
}

# Local variables
locals {
  cluster_name = "ephemera-${var.environment}"

  common_labels = {
    project     = "ephemera"
    environment = var.environment
    managed_by  = "terraform"
    owner       = var.owner
  }

  # GCP regions have zones like us-central1-a, us-central1-b
  zones = [
    "${var.gcp_region}-a",
    "${var.gcp_region}-b",
    "${var.gcp_region}-c"
  ]
}

# VPC Module
module "vpc" {
  source = "./modules/vpc"

  project_id   = var.gcp_project_id
  cluster_name = local.cluster_name
  environment  = var.environment
  region       = var.gcp_region
  labels       = local.common_labels
}

# IAM Module
module "iam" {
  source = "./modules/iam"

  project_id     = var.gcp_project_id
  cluster_name   = local.cluster_name
  environment    = var.environment
  gke_cluster_id = module.gke.cluster_id
}

# GKE Module
module "gke" {
  source = "./modules/gke"

  project_id            = var.gcp_project_id
  cluster_name          = local.cluster_name
  cluster_version       = var.cluster_version
  environment           = var.environment
  region                = var.gcp_region
  zones                 = local.zones
  network               = module.vpc.network_name
  subnetwork            = module.vpc.subnet_name
  pod_ip_range_name     = module.vpc.pod_ip_range_name
  svc_ip_range_name     = module.vpc.svc_ip_range_name
  service_account_email = module.iam.service_account_email
  node_machine_type     = var.node_machine_type
  node_disk_size_gb     = var.node_disk_size_gb
  node_min_count        = var.node_min_count
  node_max_count        = var.node_max_count
  use_preemptible       = var.use_preemptible_nodes
  labels                = local.common_labels
}

# Cloud SQL Module
module "cloudsql" {
  source = "./modules/cloudsql"

  project_id              = var.gcp_project_id
  cluster_name            = local.cluster_name
  environment             = var.environment
  region                  = var.gcp_region
  network                 = module.vpc.network_id
  private_vpc_connection  = module.vpc.private_vpc_connection
  db_tier                 = var.db_tier
  db_name                 = var.db_name
  db_username             = var.db_username
  labels                  = local.common_labels
}

# Memorystore (Redis) Module
module "memorystore" {
  source = "./modules/memorystore"

  project_id             = var.gcp_project_id
  cluster_name           = local.cluster_name
  environment            = var.environment
  region                 = var.gcp_region
  network                = module.vpc.network_id
  private_vpc_connection = module.vpc.private_vpc_connection
  redis_tier             = var.redis_tier
  redis_memory_gb        = var.redis_memory_gb
  labels                 = local.common_labels
}

# Artifact Registry Module
module "artifact_registry" {
  source = "./modules/artifact-registry"

  project_id      = var.gcp_project_id
  region          = var.gcp_region
  repository_name = "ephemera"
  labels          = local.common_labels
}

# Kubernetes Secrets Module
module "kubernetes_secrets" {
  source = "./modules/kubernetes-secrets"

  namespace = "ephemera-system"

  # Build connection strings from module outputs
  database_url = "postgresql://${module.cloudsql.username}:${module.cloudsql.password}@${module.cloudsql.private_ip}:5432/${module.cloudsql.database_name}"
  redis_url    = "rediss://:${module.memorystore.redis_auth_string}@${module.memorystore.redis_host}:${module.memorystore.redis_port}/0"

  # GitHub credentials (use placeholders for now, update later)
  github_app_id          = var.github_app_id
  github_app_clientid    = var.github_app_clientid
  github_webhook_secret  = var.github_webhook_secret
  github_app_private_key = var.github_app_private_key

  # GitHub OAuth (placeholders - update when OAuth app is created)
  github_oauth_client_id     = var.github_oauth_client_id
  github_oauth_client_secret = var.github_oauth_client_secret

  # Application secret
  secret_key = var.secret_key

  # Encryption key is auto-generated by the module

  depends_on = [module.gke]
}
