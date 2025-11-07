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
  default     = "e2-small" # Cost-optimized: 2 vCPU, 2GB RAM
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

# Application Secrets
variable "secret_key" {
  description = "Application secret key"
  type        = string
  sensitive   = true
  default     = "change-me-in-production"
}

# GitHub App Credentials (Legacy)
variable "github_app_id" {
  description = "GitHub App ID"
  type        = string
  default     = "placeholder"
}

variable "github_app_clientid" {
  description = "GitHub App Client ID"
  type        = string
  default     = "placeholder"
}

variable "github_webhook_secret" {
  description = "GitHub Webhook Secret"
  type        = string
  sensitive   = true
  default     = "placeholder"
}

variable "github_app_private_key" {
  description = "GitHub App Private Key (PEM format)"
  type        = string
  sensitive   = true
  default     = "placeholder"
}

# GitHub OAuth Credentials (New)
variable "github_oauth_client_id" {
  description = "GitHub OAuth App Client ID"
  type        = string
  default     = "placeholder-will-update-later"
}

variable "github_oauth_client_secret" {
  description = "GitHub OAuth App Client Secret"
  type        = string
  sensitive   = true
  default     = "placeholder-will-update-later"
}
