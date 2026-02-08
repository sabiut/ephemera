output "secret_name" {
  description = "Name of the Kubernetes secret"
  value       = kubernetes_secret_v1.ephemera_secrets.metadata[0].name
}

output "encryption_key" {
  description = "Generated encryption key for credentials"
  value       = random_password.encryption_key.result
  sensitive   = true
}
