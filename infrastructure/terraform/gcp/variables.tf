variable "gcp_project_id" {
  description = "GCP project ID"
  type        = string
}

variable "gcp_region" {
  description = "GCP region for resources"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "owner" {
  description = "Owner tag for resources"
  type        = string
}

# GKE Configuration
variable "cluster_version" {
  description = "GKE cluster version"
  type        = string
  default     = "1.28"
}

variable "node_machine_type" {
  description = "Machine type for GKE nodes"
  type        = string
  # e2-medium: 2 shared vCPUs (1 sustained), 4 GB. e2-small (0.5 sustained,
  # 2 GB) left too little after GKE's own system pods: under load cluster
  # DNS timed out its health checks and workers could not resolve
  # api.github.com, so PR status updates were lost.
  default = "e2-medium"
}

variable "node_disk_size_gb" {
  description = "Disk size for GKE nodes in GB"
  type        = number
  default     = 30
}

variable "node_min_count" {
  description = "Minimum number of nodes per zone"
  type        = number
  default     = 1
}

variable "node_max_count" {
  description = "Maximum number of nodes per zone"
  type        = number
  default     = 3
}

variable "use_preemptible_nodes" {
  description = "Use preemptible VMs for cost savings (up to 80% cheaper)"
  type        = bool
  default     = true
}

# Cloud SQL Configuration
variable "db_tier" {
  description = "Cloud SQL instance tier"
  type        = string
  default     = "db-f1-micro" # Free tier eligible
}

variable "db_name" {
  description = "PostgreSQL database name"
  type        = string
  default     = "ephemera"
}

variable "db_username" {
  description = "PostgreSQL username"
  type        = string
  default     = "ephemera"
}

# Memorystore (Redis) Configuration
variable "redis_tier" {
  description = "Memorystore tier (BASIC or STANDARD_HA)"
  type        = string
  default     = "BASIC" # Single node, cheaper
}

variable "redis_memory_gb" {
  description = "Memorystore memory size in GB"
  type        = number
  default     = 1
}

# NOTE: Application secrets (GitHub credentials, API keys, etc.) are managed
# by kubectl in the GitHub Actions pipeline, not by Terraform.
# See .github/workflows/deploy.yml "Apply Kubernetes manifests" step.
