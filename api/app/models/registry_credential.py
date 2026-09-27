from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class RegistryCredential(Base):
    """
    A read-only token for pulling a repository's private images.

    Scoped to the repository, not a user, so every collaborator's previews
    can use it. The secret is Fernet-encrypted and never returned by the API.
    """
    __tablename__ = "registry_credentials"
    __table_args__ = (
        UniqueConstraint("repository_full_name", "registry", name="uq_registry_credentials_repo_registry"),
    )

    id = Column(Integer, primary_key=True, index=True)
    repository_full_name = Column(String, index=True, nullable=False)
    registry = Column(String, nullable=False)          # normalised host, e.g. ghcr.io
    username = Column(String, nullable=False)
    secret_encrypted = Column(Text, nullable=False)
    created_by_login = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
