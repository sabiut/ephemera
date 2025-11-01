output "gke_cluster_name" {
  description = "GKE cluster name"
  value       = module.gke.cluster_name
}

output "gke_cluster_endpoint" {
  description = "GKE cluster endpoint"
  value       = module.gke.cluster_endpoint
  sensitive   = true
}

output "gke_cluster_ca_certificate" {
  description = "GKE cluster CA certificate"
  value       = module.gke.cluster_ca_certificate
  sensitive   = true
}

output "configure_kubectl" {
  description = "Command to configure kubectl"
  value       = "gcloud container clusters get-credentials ${module.gke.cluster_name} --region ${var.gcp_region} --project ${var.gcp_project_id}"
}

output "cloudsql_instance_name" {
  description = "Cloud SQL instance name"
  value       = module.cloudsql.instance_name
}

output "cloudsql_connection_name" {
  description = "Cloud SQL connection name for Cloud SQL Proxy"
  value       = module.cloudsql.connection_name
}

output "cloudsql_private_ip" {
  description = "Cloud SQL private IP address"
  value       = module.cloudsql.private_ip
  sensitive   = true
}

output "cloudsql_database_name" {
  description = "PostgreSQL database name"
  value       = module.cloudsql.database_name
}

output "cloudsql_username" {
  description = "PostgreSQL username"
  value       = module.cloudsql.username
  sensitive   = true
}

output "cloudsql_password_secret" {
  description = "Secret Manager secret name containing database password"
  value       = module.cloudsql.password_secret_name
}

output "redis_host" {
  description = "Memorystore Redis host"
  value       = module.memorystore.redis_host
  sensitive   = true
}

output "redis_port" {
  description = "Memorystore Redis port"
  value       = module.memorystore.redis_port
}

output "redis_connection_string" {
  description = "Redis connection string"
  value       = "redis://${module.memorystore.redis_host}:${module.memorystore.redis_port}/0"
  sensitive   = true
}

output "vpc_network_name" {
  description = "VPC network name"
  value       = module.vpc.network_name
}

output "vpc_subnet_name" {
  description = "VPC subnet name"
  value       = module.vpc.subnet_name
}

output "service_account_email" {
  description = "GKE service account email"
  value       = module.iam.service_account_email
}

output "next_steps" {
  description = "Next steps after infrastructure is created"
  value       = <<-EOT
    Infrastructure created successfully!

    Next steps:
    1. Configure kubectl:
       gcloud container clusters get-credentials ${module.gke.cluster_name} --region ${var.gcp_region} --project ${var.gcp_project_id}

    2. Get database password from Secret Manager:
       gcloud secrets versions access latest --secret="${module.cloudsql.password_secret_name}"

    3. Update application .env file with:
       DATABASE_URL=postgresql://${module.cloudsql.username}:<password>@${module.cloudsql.private_ip}:5432/${module.cloudsql.database_name}
       REDIS_URL=redis://${module.memorystore.redis_host}:${module.memorystore.redis_port}/0

    4. Deploy application to GKE cluster
  EOT
}
output "artifact_registry_repository" {
  description = "Artifact Registry repository name"
  value       = module.artifact_registry.repository_name
}

output "artifact_registry_url" {
  description = "Full URL to push Docker images"
  value       = module.artifact_registry.repository_url
}
