from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum


class DeploymentStatus(str, enum.Enum):
    """Deployment status"""
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"


class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(Integer, primary_key=True, index=True)

    # Environment reference
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    environment = relationship("Environment", back_populates="deployments")

    # Deployment details
    commit_sha = Column(String, index=True, nullable=False)
    commit_message = Column(String, nullable=True)
    status = Column(SQLEnum(DeploymentStatus), default=DeploymentStatus.QUEUED, index=True)

    # Execution details
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    logs = Column(Text, nullable=True)  # Deployment logs

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<Deployment {self.id} - {self.commit_sha[:8]} ({self.status})>"

    @property
    def duration_seconds(self) -> int | None:
        """Calculate deployment duration in seconds"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
