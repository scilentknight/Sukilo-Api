"""Represent an active application database transaction."""

from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class DatabaseTransaction:
    """Carry the SQLAlchemy session shared by atomic adapters."""

    session: Session
