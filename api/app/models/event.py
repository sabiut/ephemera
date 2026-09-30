from sqlalchemy import JSON, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.database import Base


class Event(Base):
    """
    Something that happened on the way to a working preview, kept for the
    admin metrics page (docs/managed-builds.md, "Measuring success"):
    installed, preview_ready, preview_failed, retry, setup_check_failed,
    build_wait.
    """
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, index=True, nullable=False)
    repository_full_name = Column(String, index=True, nullable=True)
    environment_id = Column(Integer, nullable=True)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
