# Managed builds: Ephemera builds each pull request's images itself
# (docs/managed-builds.md). A build runs a stranger's Dockerfile, and any
# build step can obtain its service account's token from the metadata
# server, so every repository builds as its own identity that can only:
#   - read its own source prefix in the source bucket,
#   - write its own log prefix in the logs bucket,
#   - push to its own image registry.
#
# Those identities come from a fixed pool of slots created here. The worker
# that starts builds (the controller) may act as the slot accounts and
# manage the slot registries, but holds no IAM-admin role: it cannot create
# service accounts or grant itself anything, so a compromised worker cannot
# reach the platform's own images or deploy identity.

resource "google_project_service" "cloudbuild" {
  project            = var.project_id
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
}

# ------------------------------------------------------------------ buckets

# Commit tarballs, uploaded by the worker as slot-<n>/<build-id>.tgz. Short-lived.
resource "google_storage_bucket" "source" {
  name                        = "${var.project_id}-ephemera-build-source"
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = var.labels

  lifecycle_rule {
    condition {
      age = 1
    }
    action {
      type = "Delete"
    }
  }
}

# Build logs, written by Cloud Build under slot-<n>/ and shown by Ephemera,
# so users never need Google Cloud access to read them.
resource "google_storage_bucket" "logs" {
  name                        = "${var.project_id}-ephemera-build-logs"
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = var.labels

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

# ------------------------------------------------------------------ the controller (worker)

resource "google_service_account" "controller" {
  account_id   = "ephemera-builds-controller"
  display_name = "Ephemera managed builds: starts builds"
  project      = var.project_id
}

resource "google_service_account_iam_member" "controller_workload_identity" {
  service_account_id = google_service_account.controller.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${var.controller_ksa}]"
}

# Create, read and cancel builds. Which identity a build runs as is limited
# by actAs (serviceAccountUser), granted below only on the slot accounts.
resource "google_project_iam_member" "controller_builds" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.editor"
  member  = "serviceAccount:${google_service_account.controller.email}"
}

resource "google_storage_bucket_iam_member" "controller_source" {
  bucket = google_storage_bucket.source.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.controller.email}"
}

# Reads build logs, and deletes a slot's logs before the slot is given to
# another repository (whose build account could otherwise read them).
resource "google_storage_bucket_iam_member" "controller_logs" {
  bucket = google_storage_bucket.logs.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.controller.email}"
}

# ------------------------------------------------------------------ build slots

resource "google_service_account" "slot" {
  count        = var.build_slots
  account_id   = "ephemera-build-slot-${count.index}"
  display_name = "Ephemera managed builds: slot ${count.index}"
  project      = var.project_id
}

resource "google_artifact_registry_repository" "slot" {
  provider      = google-beta
  count         = var.build_slots
  project       = var.project_id
  location      = var.region
  repository_id = "ephemera-builds-${count.index}"
  description   = "Images built by Ephemera for the repository assigned to build slot ${count.index}"
  format        = "DOCKER"
  labels        = var.labels

  # Tagged images are deleted by Ephemera when their pull request closes or
  # expires; anything left untagged (superseded layers) goes after a day.
  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "86400s"
    }
  }
}

# The slot's identity pushes to its own registry only.
resource "google_artifact_registry_repository_iam_member" "slot_writer" {
  provider   = google-beta
  count      = var.build_slots
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.slot[count.index].repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.slot[count.index].email}"
}

# The controller deletes a pull request's tags, and wipes a slot before it
# is given to another repository.
resource "google_artifact_registry_repository_iam_member" "controller_slot_admin" {
  provider   = google-beta
  count      = var.build_slots
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.slot[count.index].repository_id
  role       = "roles/artifactregistry.repoAdmin"
  member     = "serviceAccount:${google_service_account.controller.email}"
}

# The slot reads only its own source tarballs...
resource "google_storage_bucket_iam_member" "slot_source" {
  count  = var.build_slots
  bucket = google_storage_bucket.source.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.slot[count.index].email}"

  condition {
    title      = "slot-${count.index}-only"
    expression = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.source.name}/objects/slot-${count.index}/\")"
  }
}

# ...and writes only its own logs.
resource "google_storage_bucket_iam_member" "slot_logs" {
  count  = var.build_slots
  bucket = google_storage_bucket.logs.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.slot[count.index].email}"

  condition {
    title      = "slot-${count.index}-logs-only"
    expression = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.logs.name}/objects/slot-${count.index}/\")"
  }
}

# The controller may start builds as a slot account (and as nothing else).
resource "google_service_account_iam_member" "controller_acts_as_slot" {
  count              = var.build_slots
  service_account_id = google_service_account.slot[count.index].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.controller.email}"
}
