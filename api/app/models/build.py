from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class Build(Base):
    """
    One managed build: the images of a preview's commit, built by Cloud
    Build as the repository's build slot (docs/managed-builds.md).
    """
    __tablename__ = "builds"

    id = Column(Integer, primary_key=True, index=True)
    environment_id = Column(Integer, ForeignKey("environments.id", ondelete="CASCADE"), index=True, nullable=False)
    repository_full_name = Column(String, index=True, nullable=False)
    pr_number = Column(Integer, nullable=False)
    commit_sha = Column(String, nullable=False)
    slot = Column(Integer, nullable=False)
    # queued, building, succeeded, failed, timeout, cancelled
    status = Column(String, nullable=False, default="queued")
    cloud_build_id = Column(String, nullable=True)
    # {service: image} built, and {service: queued|building|done|failed} progress.
    images = Column(JSON, nullable=True)
    services = Column(JSON, nullable=True)
    failure_category = Column(String, nullable=True)
    failure_detail = Column(Text, nullable=True)
    log_object = Column(String, nullable=True)     # the full log, in the logs bucket
    log_tail = Column(Text, nullable=True)
    # Billed time, from Cloud Build's own start and finish (monthly limits).
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
