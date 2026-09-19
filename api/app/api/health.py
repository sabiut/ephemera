from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import get_db
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/")
async def health_check():
    """Basic liveness check - always returns 200 if app is running"""
    return {"status": "healthy"}

@router.get("/ready")
async def readiness_check(response: Response, db: Session = Depends(get_db)):
    """Check if service is ready (DB connection, etc.)"""
    try:
        # Test database connection (use text() for SQLAlchemy 2.x)
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        # Return 503 Service Unavailable if database is not ready.
        # The exception text can include the DSN, so it goes to the log only.
        logger.error(f"Readiness check failed: {e}")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not ready", "error": "database unavailable"}
