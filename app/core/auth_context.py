"""Represent a validated authentication principal without tenant authority."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Carry trusted identity and session authentication facts."""

    user_id: UUID
    session_id: UUID
    auth_version: int
    authentication_methods: tuple[str, ...]
    authenticated_at: datetime
    correlation_id: UUID
