from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import reader
from app.core.db import get_db
from app.models import User
from app.services import analytics

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(_: User = Depends(reader), db: Session = Depends(get_db)):
    return analytics.dashboard(db)
