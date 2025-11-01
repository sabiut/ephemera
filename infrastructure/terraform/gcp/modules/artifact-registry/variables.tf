variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the Artifact Registry repository"
  type        = string
}

variable "repository_name" {
  description = "Name of the Artifact Registry repository"
  type        = string
  default     = "ephemera"
}

variable "labels" {
  description = "Labels to apply to resources"
  type        = map(string)
  default     = {}
}
