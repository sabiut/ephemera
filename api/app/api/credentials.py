"""
Cloud credentials management endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime

from app.database import get_db
from app.models import CloudCredential, User
from app.schemas.credential import (
    CloudCredentialCreate,
    CloudCredentialUpdate,
    CloudCredentialResponse,
)
from app.core.encryption import encrypt_credentials, decrypt_credentials
from app.api.dependencies import get_current_user

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.post("/", response_model=CloudCredentialResponse, status_code=status.HTTP_201_CREATED)
def create_credential(
    credential_data: CloudCredentialCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add cloud credentials for the authenticated user.

    The credentials JSON will be encrypted before storage.
    """
    # Encrypt the credentials
    encrypted_creds = encrypt_credentials(credential_data.credentials_json)

    # Create credential record
    db_credential = CloudCredential(
        user_id=current_user.id,
        provider=credential_data.provider,
        credentials_encrypted=encrypted_creds,
        name=credential_data.name,
        description=credential_data.description,
        is_active=True,
    )

    db.add(db_credential)
    db.commit()
    db.refresh(db_credential)

    return db_credential


@router.get("/", response_model=List[CloudCredentialResponse])
def list_credentials(
    provider: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List all cloud credentials for the authenticated user.

    Optionally filter by provider.
    """
    query = db.query(CloudCredential).filter(CloudCredential.user_id == current_user.id)

    if provider:
        query = query.filter(CloudCredential.provider == provider)

    credentials = query.all()
    return credentials


@router.get("/gcp")
def get_gcp_credentials(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get decrypted GCP credentials for the authenticated user.

    This endpoint is used by GitHub Actions workflows to retrieve
    credentials using an API token.

    Returns the first active GCP credential for the user.
    """
    credential = (
        db.query(CloudCredential)
        .filter(
            CloudCredential.user_id == current_user.id,
            CloudCredential.provider == "gcp",
            CloudCredential.is_active == True,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active GCP credentials found. Please add GCP credentials in the dashboard.",
        )

    # Decrypt and return credentials
    decrypted_creds = decrypt_credentials(credential.credentials_encrypted)

    return {
        "provider": "gcp",
        "credential_id": credential.id,
        "name": credential.name,
        "credentials_json": decrypted_creds,
    }


@router.get("/{credential_id}", response_model=CloudCredentialResponse)
def get_credential(
    credential_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a specific cloud credential.

    Note: This does not return the decrypted credentials for security.
    """
    credential = (
        db.query(CloudCredential)
        .filter(
            CloudCredential.id == credential_id,
            CloudCredential.user_id == current_user.id,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )

    return credential


@router.patch("/{credential_id}", response_model=CloudCredentialResponse)
def update_credential(
    credential_id: int,
    credential_data: CloudCredentialUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update cloud credential metadata or replace credentials.
    """
    credential = (
        db.query(CloudCredential)
        .filter(
            CloudCredential.id == credential_id,
            CloudCredential.user_id == current_user.id,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )

    # Update fields
    if credential_data.name is not None:
        credential.name = credential_data.name

    if credential_data.description is not None:
        credential.description = credential_data.description

    if credential_data.is_active is not None:
        credential.is_active = credential_data.is_active

    if credential_data.credentials_json is not None:
        # Re-encrypt new credentials
        credential.credentials_encrypted = encrypt_credentials(
            credential_data.credentials_json
        )

    db.commit()
    db.refresh(credential)

    return credential


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(
    credential_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a cloud credential.
    """
    credential = (
        db.query(CloudCredential)
        .filter(
            CloudCredential.id == credential_id,
            CloudCredential.user_id == current_user.id,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )

    db.delete(credential)
    db.commit()

    return None
