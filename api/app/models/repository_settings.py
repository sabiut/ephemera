from sqlalchemy import Boolean, Column, DateTime, Integer, String
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
    updated_by_login = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
