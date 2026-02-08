# Kubernetes provider is already configured in root main.tf
# This module creates the ephemera-secrets Kubernetes secret

resource "random_password" "encryption_key" {
  length  = 32
  special = false
}

resource "kubernetes_secret_v1" "ephemera_secrets" {
  metadata {
    name      = "ephemera-secrets"
    namespace = var.namespace
  }

  data = {
    # Database connection
    DATABASE_URL = var.database_url

    # Redis connection
    REDIS_URL              = var.redis_url
    CELERY_BROKER_URL      = var.redis_url
    CELERY_RESULT_BACKEND  = var.redis_url

    # GitHub App credentials (legacy)
    GITHUB_APP_ID          = var.github_app_id
    GITHUB_APP_CLIENTID    = var.github_app_clientid
    GITHUB_WEBHOOK_SECRET  = var.github_webhook_secret
    GITHUB_APP_PRIVATE_KEY = var.github_app_private_key

    # GitHub OAuth credentials (new)
    GITHUB_OAUTH_CLIENT_ID     = var.github_oauth_client_id
    GITHUB_OAUTH_CLIENT_SECRET = var.github_oauth_client_secret

    # Application secrets
    SECRET_KEY     = var.secret_key
    ENCRYPTION_KEY = base64encode(random_password.encryption_key.result)
  }

  type = "Opaque"

  lifecycle {
    ignore_changes = [
      metadata[0].annotations,
      metadata[0].labels,
    ]
  }
}
