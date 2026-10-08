from sqlalchemy import text
from sqlalchemy.orm import Session


class HealthRepository:
    def __init__(self, db: Session):
        self.db = db

    def check_db_connection(self) -> bool:
        result = self.db.execute(text("SELECT 1")).scalar()
        return result == 1
