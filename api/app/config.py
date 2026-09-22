from functools import lru_cache
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str

    # Redis / Celery
    redis_url: str
    celery_broker_url: Optional[str] = None
    celery_result_backend: Optional[str] = None
    # Certificate verification for rediss:// brokers. Managed Redis services
    # (Memorystore, ElastiCache) use CAs that are not always in the system
    # trust store, so this defaults to off; set to true when you ship the CA.
    redis_ssl_verify: bool = False

    # GitHub App (webhooks, PR comments, repo file access)
    github_app_id: str
    github_app_clientid: Optional[str] = None
    # Either the PEM content itself or a path to a PEM file. The content form
    # is what the Kubernetes manifests inject via Secret.
    github_app_private_key: Optional[str] = None
    github_app_private_key_path: Optional[str] = None
    github_webhook_secret: str

    # GitHub OAuth (dashboard login)
    github_oauth_client_id: Optional[str] = None
    github_oauth_client_secret: Optional[str] = None
    github_oauth_redirect_uri: str = "http://localhost:8000/auth/github/callback"
    session_token_ttl_days: int = 30

    # Kubernetes
    kubeconfig_path: Optional[str] = None
    cluster_name: Optional[str] = None

    # Application
    secret_key: str
    environment: str = "development"
    base_domain: str
    # Fernet key used to encrypt stored cloud credentials
    encryption_key: Optional[str] = None
    # Comma-separated list of allowed CORS origins. Empty means same-origin only.
    cors_origins: str = ""
    # Comma-separated GitHub logins that see every environment in the API and
    # dashboard. Everyone else sees only environments for their own PRs.
    admin_github_logins: str = ""
    # How long repository-access answers from GitHub are reused. Collaborators
    # added or removed on GitHub take effect after at most this long.
    repo_access_cache_seconds: int = 300

    # How long a preview may take to become ready (pods ready, then public
    # URLs answering) before it is reported as failed.
    preview_ready_timeout_seconds: int = 300

    # Preview namespace quotas
    preview_cpu_quota: str = "1"
    preview_memory_quota: str = "2Gi"
    preview_pod_quota: str = "10"

    # AI Deployment
    ai_deployment_enabled: bool = True
    ai_provider: str = "anthropic"  # "anthropic", "openai", or "gemini"
    ai_cache_ttl: int = 3600  # seconds

    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4-20250514"

    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"

    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.0-flash"

    # AWS (only used by the EKS deployment path)
    aws_region: str = "us-west-2"
    aws_account_id: Optional[str] = None

    @model_validator(mode="after")
    def set_celery_defaults(self):
        """Set Celery URLs to Redis URL if not provided"""
        if not self.celery_broker_url:
            self.celery_broker_url = self.redis_url
        if not self.celery_result_backend:
            self.celery_result_backend = self.redis_url
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_login_set(self) -> set[str]:
        """Lower-cased admin logins; GitHub logins are case-insensitive."""
        return {o.strip().lower() for o in self.admin_github_logins.split(",") if o.strip()}


@lru_cache()
def get_settings() -> Settings:
    return Settings()


# Global settings instance
settings = get_settings()
