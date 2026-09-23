from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Enum as SQLEnum, UniqueConstraint, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum
import re


class EnvironmentStatus(str, enum.Enum):
    """Environment lifecycle states"""
    PENDING = "pending"           # Queued for creation
    PROVISIONING = "provisioning" # Being created
    READY = "ready"              # Active and accessible
    UPDATING = "updating"        # Being updated with new code
    DESTROYING = "destroying"    # Being torn down
    DESTROYED = "destroyed"      # Cleaned up
    FAILED = "failed"           # Creation/update failed


# Deployment stages in order. "waiting_for_image" only happens when a
# service's commit image is not published yet.
STAGES = ("queued", "preparing", "deploying", "waiting_for_image", "starting", "checking_https",
          "ready", "failed", "destroying", "destroyed")


class Environment(Base):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("repository_full_name", "pr_number", name="uq_environments_repo_pr"),
    )

    id = Column(Integer, primary_key=True, index=True)

    # GitHub/PR Information
    repository_full_name = Column(String, index=True, nullable=False)  # e.g., "owner/repo"
    repository_name = Column(String, nullable=False)                    # e.g., "repo"
    pr_number = Column(Integer, index=True, nullable=False)
    pr_title = Column(String, nullable=True)
    branch_name = Column(String, nullable=False)                        # e.g., "feature-branch"
    commit_sha = Column(String, index=True, nullable=False)            # Latest commit

    # Environment Details
    namespace = Column(String, unique=True, index=True, nullable=False) # K8s namespace
    environment_url = Column(String, nullable=True)                     # Primary public URL (what a reviewer opens)
    service_urls = Column(JSON, nullable=True)                          # {service: url} for every exposed service
    status = Column(SQLEnum(EnvironmentStatus), default=EnvironmentStatus.PENDING, index=True)

    # GitHub App Integration
    installation_id = Column(Integer, nullable=False)  # GitHub App installation ID

    # Owner
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    owner = relationship("User", back_populates="environments")

    # Metadata
    error_message = Column(Text, nullable=True)  # Error details if status=failed
    last_deployed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    destroyed_at = Column(DateTime(timezone=True), nullable=True)
    # Set when the PR closes, cleared when it reopens. Tasks check it under the
    # environment lock: a deploy queued before the close must not bring the
    # preview back, and a teardown queued before a reopen must not remove it.
    closed_at = Column(DateTime(timezone=True), nullable=True)

    # Progress of the current deployment, for the dashboard: a stage key
    # (see STAGES), a human detail such as which image it waits for, when the
    # stage began, and when this deployment was queued.
    stage = Column(String, nullable=True)
    stage_detail = Column(String, nullable=True)
    stage_started_at = Column(DateTime(timezone=True), nullable=True)
    deploy_started_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    deployments = relationship("Deployment", back_populates="environment", cascade="all, delete-orphan")

    @property
    def owner_login(self):
        """GitHub login of the PR author, for "opened by" in lists."""
        return self.owner.github_login if self.owner else None

    def __repr__(self):
        return f"<Environment {self.namespace} ({self.status})>"

    @property
    def is_active(self) -> bool:
        """Check if environment is active"""
        return self.status in [
            EnvironmentStatus.PROVISIONING,
            EnvironmentStatus.READY,
            EnvironmentStatus.UPDATING
        ]

    def generate_namespace(self) -> str:
        """Generate Kubernetes namespace name"""
        return build_namespace(self.repository_name, self.pr_number)


def build_namespace(repository_name: str, pr_number: int) -> str:
    """
    Build the Kubernetes namespace for a PR: pr-{number}-{repo-slug}.

    Namespaces must be DNS labels (max 63 chars, lowercase alphanumeric or '-').
    The same slug is used as the prefix of every preview hostname, so it is
    also what the manifest validator checks Ingress hosts against.
    """
    slug = re.sub(r"[^a-z0-9-]+", "-", repository_name.lower()).strip("-")[:20].rstrip("-")
    return f"pr-{pr_number}-{slug or 'repo'}"
