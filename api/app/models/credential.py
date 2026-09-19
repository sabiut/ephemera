import enum

from sqlalchemy import Boolean, Column, DateTime, Enum as SQLEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class CloudProvider(str, enum.Enum):
    """Supported cloud providers"""
    GCP = "gcp"
    AWS = "aws"
    AZURE = "azure"


class CloudCredential(Base):
    __tablename__ = "cloud_credentials"

    id = Column(Integer, primary_key=True, index=True)

    # Owner
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="cloud_credentials")

    # Provider
    provider = Column(SQLEnum(CloudProvider), nullable=False, index=True)

    # Credentials (Fernet-encrypted JSON)
    credentials_encrypted = Column(Text, nullable=False)

    # Optional name/description
    name = Column(String, nullable=True)  # e.g., "Production GCP", "Dev AWS"
    description = Column(String, nullable=True)

    # Metadata
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<CloudCredential {self.provider} for user {self.user_id}>"
