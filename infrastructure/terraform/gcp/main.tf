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

  # Uncomment to use GCS backend for remote state (recommended for team)
  # backend "gcs" {
  #   bucket = "ephemera-terraform-state"
  #   prefix = "gcp/terraform.tfstate"
  # }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

provider "google-beta" {
  project = var.gcp_project_id
  region  = var.gcp_region
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
