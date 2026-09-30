from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.database import Base


class RepositorySettings(Base):
    """Per-repository preferences, set in the dashboard by its collaborators."""
    __tablename__ = "repository_settings"

    id = Column(Integer, primary_key=True, index=True)
    repository_full_name = Column(String, unique=True, index=True, nullable=False)
    # Only the repository's collaborators (and admins) may open its previews,
    # after signing in with GitHub. Off: anyone with the link.
    protect_previews = Column(Boolean, default=False, nullable=False)
    # Managed builds: on only after a collaborator confirmed the detected
    # build plan; build_plan_confirmed is what they confirmed (the services
    # to build, with context, Dockerfile and target), so later changes to
    # compose can be shown as differences.
    managed_builds_enabled = Column(Boolean, default=False, nullable=False)
    build_plan_confirmed = Column(JSON, nullable=True)
    build_plan_confirmed_by = Column(String, nullable=True)
    build_plan_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    updated_by_login = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
