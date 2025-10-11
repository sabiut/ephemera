output "nlb_dns_name" {
  description = "DNS name of the Network Load Balancer"
  value       = aws_lb.ingress.dns_name
}

output "nlb_arn" {
  description = "ARN of the Network Load Balancer"
  value       = aws_lb.ingress.arn
}

output "nlb_zone_id" {
  description = "Canonical hosted zone ID of the load balancer"
  value       = aws_lb.ingress.zone_id
}

output "http_target_group_arn" {
  description = "ARN of the HTTP target group"
  value       = aws_lb_target_group.http.arn
}

output "https_target_group_arn" {
  description = "ARN of the HTTPS target group"
  value       = aws_lb_target_group.https.arn
}
