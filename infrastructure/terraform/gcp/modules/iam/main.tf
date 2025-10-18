resource "google_service_account" "gke" {
  account_id   = "${var.cluster_name}-gke-sa"
  display_name = "Service Account for GKE cluster ${var.cluster_name}"
  project      = var.project_id
}

# Grant necessary roles to the GKE service account
resource "google_project_iam_member" "gke_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.gke.email}"
}

resource "google_project_iam_member" "gke_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.gke.email}"
}

resource "google_project_iam_member" "gke_monitoring_viewer" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.gke.email}"
}

resource "google_project_iam_member" "gke_resource_metadata_writer" {
  project = var.project_id
  role    = "roles/stackdriver.resourceMetadata.writer"
  member  = "serviceAccount:${google_service_account.gke.email}"
}

# Grant Artifact Registry reader for pulling container images
resource "google_project_iam_member" "gke_artifact_registry_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.gke.email}"
}

# Service account for workload identity (pods)
resource "google_service_account" "workload" {
  account_id   = "${var.cluster_name}-workload-sa"
  display_name = "Service Account for workload identity ${var.cluster_name}"
  project      = var.project_id
}

# Grant Secret Manager access to workload service account
resource "google_project_iam_member" "workload_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.workload.email}"
}

# Grant Cloud SQL client access
resource "google_project_iam_member" "workload_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.workload.email}"
}

# Allow Kubernetes service accounts to use GCP service accounts (Workload Identity)
resource "google_service_account_iam_member" "workload_identity_binding" {
  service_account_id = google_service_account.workload.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[default/ephemera-api]"

  # This depends on the GKE cluster existing first
  depends_on = [var.gke_cluster_id]
}

# Service account for GitHub Actions kubectl runner
resource "google_service_account" "kubectl_runner" {
  account_id   = "gke-kubectl-runner"
  display_name = "Service Account for GitHub Actions kubectl runner"
  project      = var.project_id
}

# Grant GKE cluster developer permissions for basic cluster access
resource "google_project_iam_member" "kubectl_runner_container_developer" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.kubectl_runner.email}"
}

# Grant GKE cluster admin permissions for managing Kubernetes resources (ClusterRoles, etc.)
resource "google_project_iam_member" "kubectl_runner_container_admin" {
  project = var.project_id
  role    = "roles/container.admin"
  member  = "serviceAccount:${google_service_account.kubectl_runner.email}"
}

# Grant storage admin for GCR/Artifact Registry
resource "google_project_iam_member" "kubectl_runner_storage_admin" {
  project = var.project_id
  role    = "roles/storage.admin"
  member  = "serviceAccount:${google_service_account.kubectl_runner.email}"
}

# Grant Artifact Registry writer for pushing images
resource "google_project_iam_member" "kubectl_runner_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.kubectl_runner.email}"
}

# Grant Artifact Registry repo admin for creating repositories
resource "google_project_iam_member" "kubectl_runner_artifact_admin" {
  project = var.project_id
  role    = "roles/artifactregistry.repoAdmin"
  member  = "serviceAccount:${google_service_account.kubectl_runner.email}"
}
