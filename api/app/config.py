from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional
from pydantic import model_validator

class Settings(BaseSettings):
    # Database
    database_url: str

    # Redis
    redis_url: str

    # Celery
    celery_broker_url: Optional[str] = None
    celery_result_backend: Optional[str] = None

    # GitHub
    github_app_id: str
    github_app_clientid: str
    github_app_private_key_path: str
    github_webhook_secret: str

    # Kubernetes
    kubeconfig_path: str
    cluster_name: str

    # Application
    secret_key: str
    environment: str = "development"
    base_domain: str

    # AWS
    aws_region: str = "us-west-2"
    aws_account_id: str

    @model_validator(mode='after')
    def set_celery_defaults(self):
        """Set Celery URLs to Redis URL if not provided"""
        if not self.celery_broker_url:
            self.celery_broker_url = self.redis_url
        if not self.celery_result_backend:
            self.celery_result_backend = self.redis_url
        return self

    class Config:
        env_file = ".env"
        case_sensitive = False

@lru_cache()
def get_settings():
    return Settings()

# Global settings instance
settings = get_settings()
