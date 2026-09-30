from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class Build(Base):
    """
    One managed build: the images of a preview's commit, built by Cloud
    Build as the repository's build slot (docs/managed-builds.md).
    """
    __tablename__ = "builds"

    id = Column(Integer, primary_key=True, index=True)
    # Kept (with no preview) when the preview's record is deleted: the
    # repository's used build minutes must not come back.
    environment_id = Column(Integer, ForeignKey("environments.id", ondelete="SET NULL"), index=True, nullable=True)
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
    # When the images' tags were deleted from the slot registry (the preview
    # moved on to a newer commit or was removed); untagged images are then
    # deleted by the registry's cleanup policy within a day.
    images_deleted_at = Column(DateTime(timezone=True), nullable=True)


class BuildApproval(Base):
    """
    A collaborator's approval to build one commit of a fork's pull request.
    Fork code builds as the repository's slot, which can push its images, so
    every new commit needs approving again.
    """
    __tablename__ = "build_approvals"
    __table_args__ = (UniqueConstraint("repository_full_name", "pr_number", "commit_sha",
                                       name="uq_build_approvals_commit"),)

    id = Column(Integer, primary_key=True, index=True)
    repository_full_name = Column(String, nullable=False)
    pr_number = Column(Integer, nullable=False)
    commit_sha = Column(String, nullable=False)
    approved_by_login = Column(String, nullable=False)
    approved_at = Column(DateTime(timezone=True), server_default=func.now())
