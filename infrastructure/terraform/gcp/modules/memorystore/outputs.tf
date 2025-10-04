output "redis_id" {
  description = "Redis instance ID"
  value       = google_redis_instance.cache.id
}

output "redis_host" {
  description = "Redis instance host"
  value       = google_redis_instance.cache.host
}

output "redis_port" {
  description = "Redis instance port"
  value       = google_redis_instance.cache.port
}

output "redis_auth_string" {
  description = "Redis AUTH string"
  value       = google_redis_instance.cache.auth_string
  sensitive   = true
}

output "redis_connection_string" {
  description = "Redis connection string"
  value       = "redis://${google_redis_instance.cache.host}:${google_redis_instance.cache.port}/0"
  sensitive   = true
}
