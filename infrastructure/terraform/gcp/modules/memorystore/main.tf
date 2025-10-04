resource "google_redis_instance" "cache" {
  name           = "${var.cluster_name}-redis"
  display_name   = "${var.cluster_name} Redis Instance"
  tier           = var.redis_tier
  memory_size_gb = var.redis_memory_gb
  region         = var.region
  project        = var.project_id

  # Redis version
  redis_version = "REDIS_7_0"

  # Network
  authorized_network = var.network
  connect_mode       = "PRIVATE_SERVICE_ACCESS"

  # Redis configuration
  redis_configs = {
    maxmemory-policy = "allkeys-lru"
  }

  # Maintenance policy
  maintenance_policy {
    weekly_maintenance_window {
      day = "SUNDAY"
      start_time {
        hours   = 3
        minutes = 0
      }
    }
  }

  # Labels
  labels = var.labels

  # Auth
  auth_enabled = true

  # Transit encryption
  transit_encryption_mode = "SERVER_AUTHENTICATION"
}
