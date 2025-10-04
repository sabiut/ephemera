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
}
