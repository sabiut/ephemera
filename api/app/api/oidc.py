"""
POST /api/v1/oidc/token: a GitHub Actions job exchanges its OIDC identity
token for a short-lived pipeline token (docs/ci-pipeline.md, step 6). No
other credential: the signed identity token is the proof.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services import oidc

router = APIRouter()


class ExchangeRequest(BaseModel):
    token: str


@router.post("/oidc/token")
def exchange_token(body: ExchangeRequest, db: Session = Depends(get_db)):
    try:
        exchanged = oidc.exchange(db, body.token)
    except oidc.OIDCError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {"token": exchanged.token, "repository": exchanged.repository, "actor": exchanged.actor,
            "expires_in": settings.oidc_token_minutes * 60, "scope": "previews"}
