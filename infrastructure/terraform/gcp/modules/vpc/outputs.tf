output "network_id" {
  description = "VPC network ID"
  value       = google_compute_network.main.id
}

output "network_name" {
  description = "VPC network name"
  value       = google_compute_network.main.name
}

output "network_self_link" {
  description = "VPC network self link"
  value       = google_compute_network.main.self_link
}

output "subnet_id" {
  description = "Subnet ID"
  value       = google_compute_subnetwork.main.id
}

output "subnet_name" {
  description = "Subnet name"
  value       = google_compute_subnetwork.main.name
}

output "subnet_self_link" {
  description = "Subnet self link"
  value       = google_compute_subnetwork.main.self_link
}

output "pod_ip_range_name" {
  description = "Secondary IP range name for pods"
  value       = google_compute_subnetwork.main.secondary_ip_range[0].range_name
}

output "svc_ip_range_name" {
  description = "Secondary IP range name for services"
  value       = google_compute_subnetwork.main.secondary_ip_range[1].range_name
}

output "private_vpc_connection" {
  description = "Private VPC connection for Cloud SQL"
  value       = google_service_networking_connection.private_vpc_connection.network
}
