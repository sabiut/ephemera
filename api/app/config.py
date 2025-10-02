from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # Database
    database_url: str

    # Redis
    redis_url: str

    # GitHub
    github_app_id: str
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

    class Config:
        env_file = ".env"
        case_sensitive = False

@lru_cache()
def get_settings():
    return Settings()
