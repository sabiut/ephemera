from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import secrets


class APIToken(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)

    # Owner
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="api_tokens")

    # Token
    token = Column(String, unique=True, index=True, nullable=False)  # e.g., "eph_abc123xyz"
    token_prefix = Column(String, index=True, nullable=False)  # First 8 chars for display

    # Optional name/description
    name = Column(String, nullable=True)  # e.g., "GitHub Actions - my-app"
    description = Column(Text, nullable=True)

    # Metadata
    is_active = Column(Integer, default=1)  # SQLite doesn't have native boolean
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # Optional expiration
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<APIToken {self.token_prefix}... for user {self.user_id}>"

    @staticmethod
    def generate_token() -> str:
        """Generate a secure random API token"""
        # Format: eph_<32 random hex chars>
        random_part = secrets.token_hex(32)
        return f"eph_{random_part}"

    @property
    def is_valid(self) -> bool:
        """Check if token is still valid"""
        if not self.is_active or self.revoked_at:
            return False
        if self.expires_at and self.expires_at < func.now():
            return False
        return True
