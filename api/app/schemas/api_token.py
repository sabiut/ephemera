from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class APITokenBase(BaseModel):
    """Base schema for API tokens"""
    name: Optional[str] = None
    description: Optional[str] = None


class APITokenCreate(APITokenBase):
    """Schema for creating API tokens"""
    expires_at: Optional[datetime] = Field(None, description="Optional expiration date")


class APITokenResponse(APITokenBase):
    """Schema for API token response"""
    id: int
    user_id: int
    token_prefix: str  # Only show first 8 chars
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class APITokenWithToken(APITokenResponse):
    """Schema with full token (only returned once during creation)"""
    token: str = Field(..., description="Full token - save this! It won't be shown again")


class APITokenRevoke(BaseModel):
    """Schema for revoking a token"""
    reason: Optional[str] = None
