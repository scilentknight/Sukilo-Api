import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class HealthService:
    def __init__(self, db: Session):
        self.db = db

    def test_database_connection(self) -> dict:
        try:
            connected = self.db.execute(text("SELECT 1")).scalar() == 1
            if connected:
                return {"database": "connected"}
            return {"database": "error", "message": "Database query did not return expected result"}
        except SQLAlchemyError as exc:
            logger.error("Database connection error: %s", exc)
            return {
                "database": "disconnected",
                "message": "Unable to connect to PostgreSQL database",
            }
        except Exception as exc:
            logger.error("Unexpected error during database health check: %s", exc)
            return {
                "database": "disconnected",
                "message": "Database service is temporarily unavailable",
            }
