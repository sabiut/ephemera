variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "cluster_name" {
  description = "GKE cluster name"
  type        = string
}

variable "cluster_version" {
  description = "GKE cluster version"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
}

variable "zones" {
  description = "GCP zones for node pools"
  type        = list(string)
}

variable "network" {
  description = "VPC network name"
  type        = string
}

variable "subnetwork" {
  description = "VPC subnetwork name"
  type        = string
}

variable "pod_ip_range_name" {
  description = "Secondary IP range name for pods"
  type        = string
}

variable "svc_ip_range_name" {
  description = "Secondary IP range name for services"
  type        = string
}

variable "node_machine_type" {
  description = "Machine type for nodes"
  type        = string
}

variable "node_disk_size_gb" {
  description = "Disk size for nodes in GB"
  type        = number
}

variable "node_min_count" {
  description = "Minimum number of nodes"
  type        = number
}

variable "node_max_count" {
  description = "Maximum number of nodes"
  type        = number
}

variable "use_preemptible" {
  description = "Use preemptible VMs"
  type        = bool
}

variable "service_account_email" {
  description = "Service account email for GKE nodes"
  type        = string
}

variable "labels" {
  description = "Labels to apply to resources"
  type        = map(string)
  default     = {}
}
