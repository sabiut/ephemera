"""
API token management endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime

from app.database import get_db
from app.models import APIToken, User
from app.schemas.api_token import (
    APITokenCreate,
    APITokenResponse,
    APITokenWithToken,
    APITokenRevoke,
)
from app.api.dependencies import get_current_user

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.post("/", response_model=APITokenWithToken, status_code=status.HTTP_201_CREATED)
def create_token(
    token_data: APITokenCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a new API token for the authenticated user.

    WARNING: The full token is only shown once! Save it securely.
    """
    # Generate token
    token = APIToken.generate_token()
    token_prefix = token[:8]  # First 8 chars for display

    # Create token record
    db_token = APIToken(
        user_id=current_user.id,
        token=token,
        token_prefix=token_prefix,
        name=token_data.name,
        description=token_data.description,
        expires_at=token_data.expires_at,
        is_active=True,
    )

    db.add(db_token)
    db.commit()
    db.refresh(db_token)

    # Return response with full token
    response = APITokenWithToken.model_validate(db_token)
    response.token = token  # Add full token to response

    return response


@router.get("/", response_model=List[APITokenResponse])
def list_tokens(
    include_revoked: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List all API tokens for the authenticated user.

    By default, only shows active tokens. Set include_revoked=true to see all.
    """
    query = db.query(APIToken).filter(APIToken.user_id == current_user.id)

    if not include_revoked:
        query = query.filter(APIToken.is_active == True, APIToken.revoked_at.is_(None))

    tokens = query.order_by(APIToken.created_at.desc()).all()
    return tokens


@router.get("/{token_id}", response_model=APITokenResponse)
def get_token(
    token_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get details of a specific API token.

    Note: The full token value is never returned after creation.
    """
    token = (
        db.query(APIToken)
        .filter(
            APIToken.id == token_id,
            APIToken.user_id == current_user.id,
        )
        .first()
    )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )

    return token


@router.post("/{token_id}/revoke", response_model=APITokenResponse)
def revoke_token(
    token_id: int,
    revoke_data: APITokenRevoke = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Revoke an API token.

    This immediately invalidates the token.
    """
    token = (
        db.query(APIToken)
        .filter(
            APIToken.id == token_id,
            APIToken.user_id == current_user.id,
        )
        .first()
    )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )

    if token.revoked_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token is already revoked",
        )

    # Revoke the token
    token.revoked_at = datetime.utcnow()
    token.is_active = False

    db.commit()
    db.refresh(token)

    return token


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_token(
    token_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Permanently delete an API token.

    This removes the token from the database entirely.
    """
    token = (
        db.query(APIToken)
        .filter(
            APIToken.id == token_id,
            APIToken.user_id == current_user.id,
        )
        .first()
    )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )

    db.delete(token)
    db.commit()

    return None
