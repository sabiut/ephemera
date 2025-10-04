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

variable "region" {
  description = "GCP region"
  type        = string
}

variable "network" {
  description = "VPC network ID"
  type        = string
}

variable "redis_tier" {
  description = "Memorystore tier (BASIC or STANDARD_HA)"
  type        = string
}

variable "redis_memory_gb" {
  description = "Memorystore memory size in GB"
  type        = number
}

variable "labels" {
  description = "Labels to apply to resources"
  type        = map(string)
  default     = {}
}
