variable "namespace" {
  description = "Kubernetes namespace for the secret"
  type        = string
  default     = "ephemera-system"
}

variable "database_url" {
  description = "PostgreSQL connection URL"
  type        = string
  sensitive   = true
}

variable "redis_url" {
  description = "Redis connection URL"
  type        = string
  sensitive   = true
}

variable "github_app_id" {
  description = "GitHub App ID"
  type        = string
  default     = "placeholder"
}

variable "github_app_clientid" {
  description = "GitHub App Client ID"
  type        = string
  default     = "placeholder"
}

variable "github_webhook_secret" {
  description = "GitHub Webhook Secret"
  type        = string
  default     = "placeholder"
  sensitive   = true
}

variable "github_app_private_key" {
  description = "GitHub App Private Key"
  type        = string
  default     = "placeholder"
  sensitive   = true
}

variable "github_oauth_client_id" {
  description = "GitHub OAuth Client ID"
  type        = string
  default     = "placeholder-will-update-later"
}

variable "github_oauth_client_secret" {
  description = "GitHub OAuth Client Secret"
  type        = string
  default     = "placeholder-will-update-later"
  sensitive   = true
}

variable "secret_key" {
  description = "Application secret key"
  type        = string
  sensitive   = true
}
