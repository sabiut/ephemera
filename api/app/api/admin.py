"""
Platform views for Ephemera's operators (ADMIN_GITHUB_LOGINS), not users.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, is_admin
from app.database import get_db
from app.models import User
from app.services import metrics

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/metrics")
def platform_metrics(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    """
    Whether previews work without help, and what managed builds cost
    (docs/managed-builds.md, "Measuring success"). Admins only; others get
    404, as for anything they may not see.
    """
    if not is_admin(current_user):
        raise HTTPException(status_code=404, detail="Not found")
    return metrics.compute(db, days=days)
