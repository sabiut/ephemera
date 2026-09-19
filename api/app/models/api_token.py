import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class APIToken(Base):
    """
    Bearer token for the REST API and the dashboard.

    Only a SHA-256 hash of the token is stored. The raw value is shown to the
    user once, at creation time, and can never be recovered from the database.
    """

    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)

    # Owner
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="api_tokens")

    # Token
    token_hash = Column(String, unique=True, index=True, nullable=False)
    token_prefix = Column(String, index=True, nullable=False)  # First 8 chars for display

    # Optional name/description
    name = Column(String, nullable=True)  # e.g., "GitHub Actions - my-app"
    description = Column(Text, nullable=True)

    # Metadata
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # Optional expiration
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<APIToken {self.token_prefix}... for user {self.user_id}>"

    @staticmethod
    def generate_token() -> str:
        """Generate a secure random API token of the form eph_<64 hex chars>."""
        return f"eph_{secrets.token_hex(32)}"

    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Hash a raw token for storage and lookup."""
        return hashlib.sha256(raw_token.encode()).hexdigest()

    @property
    def is_valid(self) -> bool:
        """Check if token is still usable."""
        if not self.is_active or self.revoked_at:
            return False
        if self.expires_at:
            expires = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                return False
        return True
