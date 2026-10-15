import logging
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import Employee
from app.schemas import HealthResponse, EmployeeOut

router = APIRouter(tags=["Health"])
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)):
    """Health check endpoint to verify database connectivity and operational status."""
    db_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Health check database query failed: %s", e)
        db_status = "disconnected"

    return HealthResponse(
        status="ok" if db_status == "connected" else "degraded",
        database=db_status,
        timezone=settings.timezone,
        mode=settings.intent_mode,
    )


@router.get("/employees", response_model=List[EmployeeOut])
def get_employees(db: Session = Depends(get_db)):
    """Return all employees sorted by user_id from the database."""
    return db.query(Employee).order_by(Employee.user_id.asc()).all()
