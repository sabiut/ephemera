resource "google_artifact_registry_repository" "ephemera" {
  location      = var.region
  repository_id = var.repository_name
  description   = "Docker repository for Ephemera application"
  format        = "DOCKER"

  labels = var.labels
}
