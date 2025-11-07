from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from app.models.credential import CloudProvider


class CloudCredentialBase(BaseModel):
    """Base schema for cloud credentials"""
    provider: CloudProvider
    name: Optional[str] = None
    description: Optional[str] = None


class CloudCredentialCreate(CloudCredentialBase):
    """Schema for creating cloud credentials"""
    credentials_json: str = Field(..., description="JSON string of cloud credentials (will be encrypted)")


class CloudCredentialUpdate(BaseModel):
    """Schema for updating cloud credentials"""
    name: Optional[str] = None
    description: Optional[str] = None
    credentials_json: Optional[str] = None
    is_active: Optional[bool] = None


class CloudCredentialResponse(CloudCredentialBase):
    """Schema for cloud credential response (no sensitive data)"""
    id: int
    user_id: int
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CloudCredentialWithDecrypted(CloudCredentialResponse):
    """Schema with decrypted credentials (use carefully, only for internal operations)"""
    credentials_json: str
