"""Define composition and optional security-service ports."""

from typing import Protocol

from sqlalchemy.orm import Session

from app.modules.identity.application import IdentityApplication


class IdentityApplicationFactory(Protocol):
    """Build request-scoped identity services over one database session."""

    def __call__(self, db: Session) -> IdentityApplication:
        """Build identity services sharing the supplied transaction."""
        ...


class BreachedPasswordChecker(Protocol):
    """Check passwords against a protected breach corpus."""

    def is_breached(self, password: str) -> bool:
        """Return whether a password appears in the breach corpus."""
        ...


class SecretResolver(Protocol):
    """Resolve secret-manager references without logging secret values."""

    def resolve(self, reference: str) -> str:
        """Resolve one configured secret reference."""
        ...
