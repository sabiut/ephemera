output "instance_name" {
  description = "Cloud SQL instance name"
  value       = google_sql_database_instance.postgres.name
}

output "instance_connection_name" {
  description = "Cloud SQL instance connection name"
  value       = google_sql_database_instance.postgres.connection_name
}

output "connection_name" {
  description = "Cloud SQL connection name for Cloud SQL Proxy"
  value       = google_sql_database_instance.postgres.connection_name
}

output "private_ip" {
  description = "Cloud SQL private IP address"
  value       = google_sql_database_instance.postgres.private_ip_address
}

output "database_name" {
  description = "PostgreSQL database name"
  value       = google_sql_database.database.name
}

output "username" {
  description = "PostgreSQL username"
  value       = google_sql_user.user.name
}

output "password" {
  description = "PostgreSQL password (sensitive)"
  value       = random_password.db_password.result
  sensitive   = true
}

output "password_secret_name" {
  description = "Secret Manager secret name containing database password"
  value       = google_secret_manager_secret.db_password.secret_id
}
