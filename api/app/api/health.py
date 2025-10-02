from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db

router = APIRouter()

@router.get("/")
async def health_check():
    return {"status": "healthy"}

@router.get("/ready")
async def readiness_check(db: Session = Depends(get_db)):
    """Check if service is ready (DB connection, etc.)"""
    try:
        # Test database connection
        db.execute("SELECT 1")
        return {"status": "ready"}
    except Exception as e:
        return {"status": "not ready", "error": str(e)}
