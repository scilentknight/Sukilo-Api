from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.health import DatabaseTestResponse, HealthResponse
from app.services.health_service import HealthService

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
def health_check():
    return {"status": "healthy"}


@router.get("/database-test", response_model=DatabaseTestResponse)
def database_test(db: Session = Depends(get_db)):
    service = HealthService(db)
    result = service.test_database_connection()
    if result.get("database") != "connected":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=result.get("message", "Database connection failed"),
        )
    return result
