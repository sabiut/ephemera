output "service_account_email" {
  description = "GKE service account email"
  value       = google_service_account.gke.email
}

output "service_account_name" {
  description = "GKE service account name"
  value       = google_service_account.gke.name
}

output "workload_service_account_email" {
  description = "Workload identity service account email"
  value       = google_service_account.workload.email
}

output "workload_service_account_name" {
  description = "Workload identity service account name"
  value       = google_service_account.workload.name
}

output "kubectl_runner_service_account_email" {
  description = "GitHub Actions kubectl runner service account email"
  value       = google_service_account.kubectl_runner.email
}

output "kubectl_runner_service_account_name" {
  description = "GitHub Actions kubectl runner service account name"
  value       = google_service_account.kubectl_runner.name
}
