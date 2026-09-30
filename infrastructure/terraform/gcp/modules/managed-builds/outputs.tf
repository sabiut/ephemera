output "controller_service_account" {
  description = "Google service account the worker uses (via Workload Identity) to start builds"
  value       = google_service_account.controller.email
}


output "slots" {
  description = "Build slots: the service account each build runs as, its registry and its buckets"
  value = [
    for i in range(var.build_slots) : {
      index           = i
      service_account = google_service_account.slot[i].email
      registry        = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.slot[i].repository_id}"
      source_bucket   = google_storage_bucket.source[i].name
      logs_bucket     = google_storage_bucket.logs[i].name
    }
  ]
}
