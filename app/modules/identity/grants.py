"""Create and consume purpose-bound authorization grants."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from app.core.security import DigestService, generate_opaque_token
from app.modules.identity.models import AuthGrant
from app.modules.identity.types import GrantPurpose


class AuthGrantRepositoryProtocol(Protocol):
    """Describe authorization grant persistence."""

    def add(self, grant: AuthGrant) -> None:
        """Stage a grant."""
        ...

    def get_by_token_hash_for_update(
        self,
        token_hash: str,
    ) -> AuthGrant | None:
        """Lock and return a grant by token digest."""
        ...

    def flush(self) -> None:
        """Flush staged grant changes."""
        ...


class AuthGrantError(Exception):
    """Report a safe grant validation failure."""


class AuthGrantService:
    """Issue and atomically consume single-use grants."""

    def __init__(
        self,
        *,
        repository: AuthGrantRepositoryProtocol,
        digests: DigestService,
        clock: Callable[[], datetime],
        token_source: Callable[[], str] = generate_opaque_token,
    ) -> None:
        """Initialize grant dependencies."""
        self.repository = repository
        self.digests = digests
        self.clock = clock
        self.token_source = token_source

    def create(
        self,
        *,
        user_id: UUID,
        session_id: UUID | None,
        purpose: GrantPurpose,
        action_class: str | None,
        subject_hash: str | None,
        authentication_methods: list[str],
        correlation_id: UUID,
        ttl: timedelta,
    ) -> str:
        """Create a protected grant and return its raw token once."""
        now = self.clock()
        raw_token = self.token_source()
        token_hash = self.digests.digest("auth-grant", raw_token)
        grant = AuthGrant(
            grant_id=uuid4(),
            user_id=user_id,
            session_id=session_id,
            purpose=purpose,
            action_class=action_class,
            subject_hash=subject_hash,
            token_hash=token_hash,
            authentication_methods=list(authentication_methods),
            issued_at=now,
            expires_at=now + ttl,
            correlation_id=correlation_id,
        )
        self.repository.add(grant)
        self.repository.flush()
        return raw_token

    def consume(
        self,
        raw_token: str,
        *,
        user_id: UUID | None,
        session_id: UUID | None,
        purpose: GrantPurpose,
        action_class: str | None,
        subject_hash: str | None,
    ) -> AuthGrant:
        """Validate grant bindings and mark the grant consumed."""
        token_hash = self.digests.digest("auth-grant", raw_token)
        grant = self.repository.get_by_token_hash_for_update(token_hash)
        now = self.clock()
        if (
            grant is None
            or grant.consumed_at is not None
            or grant.revoked_at is not None
            or grant.expires_at <= now
        ):
            raise AuthGrantError("Authorization grant is unavailable.")
        if (
            (user_id is not None and grant.user_id != user_id)
            or grant.session_id != session_id
            or grant.purpose != purpose
            or grant.action_class != action_class
            or grant.subject_hash != subject_hash
        ):
            raise AuthGrantError("Authorization grant binding is invalid.")
        grant.consumed_at = now
        self.repository.flush()
        return grant
