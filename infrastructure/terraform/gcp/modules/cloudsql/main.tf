# Random suffix for unique instance name
resource "random_id" "db_suffix" {
  byte_length = 4
}

# Database instance
resource "google_sql_database_instance" "postgres" {
  name             = "${var.cluster_name}-db-${random_id.db_suffix.hex}"
  database_version = "POSTGRES_15"
  region           = var.region
  project          = var.project_id

  # Delete protection - disable for dev/test environments
  deletion_protection = false

  settings {
    tier              = var.db_tier
    availability_type = "ZONAL" # Use "REGIONAL" for HA in production
    disk_size         = 10      # GB
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    # Backup configuration
    backup_configuration {
      enabled                        = true
      start_time                     = "02:00"
      point_in_time_recovery_enabled = false # Enable for production
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 7
      }
    }

    # IP configuration - private IP only
    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    # Maintenance window
    maintenance_window {
      day          = 7 # Sunday
      hour         = 3
      update_track = "stable"
    }

    # Database flags for performance
    database_flags {
      name  = "max_connections"
      value = "100"
    }

    database_flags {
      name  = "shared_buffers"
      value = "32768" # 256MB (in 8KB pages)
    }

    # Insights config
    insights_config {
      query_insights_enabled  = true
      query_string_length     = 1024
      record_application_tags = true
      record_client_address   = true
    }
  }

  depends_on = [var.private_vpc_connection]
}

# Database
resource "google_sql_database" "database" {
  name     = var.db_name
  instance = google_sql_database_instance.postgres.name
  project  = var.project_id
}

# Random password
resource "random_password" "db_password" {
  length  = 32
  special = true
}

# Database user
resource "google_sql_user" "user" {
  name     = var.db_username
  instance = google_sql_database_instance.postgres.name
  password = random_password.db_password.result
  project  = var.project_id
}

# Store password in Secret Manager
resource "google_secret_manager_secret" "db_password" {
  secret_id = "${var.cluster_name}-db-password"
  project   = var.project_id

  replication {
    auto {}
  }

  labels = var.labels
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result
}
