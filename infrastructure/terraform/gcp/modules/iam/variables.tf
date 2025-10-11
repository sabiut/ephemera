variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "cluster_name" {
  description = "Name prefix for all resources"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "gke_cluster_id" {
  description = "GKE cluster ID to create dependency"
  type        = string
  default     = ""
}
