variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "Region for builds, their source and their images (next to the cluster)"
  type        = string
}

variable "build_slots" {
  description = "How many repositories can have managed builds at once. Each slot is a build service account and an image registry that only it can write."
  type        = number
  default     = 10
}

variable "controller_ksa" {
  description = "Kubernetes service account (namespace/name) that starts builds: the Celery worker"
  type        = string
  default     = "ephemera-system/ephemera-worker"
}

variable "labels" {
  description = "Labels applied to resources"
  type        = map(string)
  default     = {}
}
