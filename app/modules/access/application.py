"""Compose transaction-scoped access-control services."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.security import DigestService
from app.core.transactions import DatabaseTransaction
from app.modules.access.cache import AuthorizationCache, CacheBackend
from app.modules.access.policies import (
    AssignmentProjection,
    AuthorizationRequest,
    PolicyEvaluator,
    ResourceScope,
)
from app.modules.access.repository import AssignmentRepository
from app.modules.access.service import AssignmentService
from app.modules.identity.grants import AuthGrantService
from app.modules.identity.repository import (
    AccountRepository,
    AuthGrantRepository,
)
from app.ports.audit import AuditWriter, NoOpAuditWriter
from app.ports.country import CountryReader


class AuthorizationDeniedError(Exception):
    """Report a denied permission decision."""


class AuthorizationProjectionService:
    """Resolve current authorization projections through a safe cache."""

    def __init__(
        self,
        *,
        repository: AssignmentRepository,
        cache: AuthorizationCache,
        clock: Callable[[], datetime],
        evaluator: PolicyEvaluator | None = None,
    ) -> None:
        """Initialize projection dependencies."""
        self.repository = repository
        self.cache = cache
        self.evaluator = evaluator or PolicyEvaluator(clock)
        self.clock = clock

    def resolve(
        self,
        user_id: UUID,
        auth_version: int,
    ) -> list[AssignmentProjection]:
        """Return cached or current database authorization state."""
        cached = self.cache.get(user_id, auth_version)
        if cached is not None:
            return cached
        projections = self.repository.list_active_projections(
            user_id,
            self.clock(),
        )
        self.cache.put(user_id, auth_version, projections)
        return projections

    def require_permission(
        self,
        context: AuthContext,
        permission_code: str,
        scope: ResourceScope,
    ) -> None:
        """Require one permission against a trusted resource scope."""
        projections = self.resolve(context.user_id, context.auth_version)
        request = AuthorizationRequest(context, permission_code, scope, None)
        if not self.evaluator.evaluate(request, projections).allowed:
            raise AuthorizationDeniedError("Permission is denied.")


@dataclass(frozen=True, slots=True)
class AccessApplication:
    """Expose request-scoped access-control use cases."""

    assignments: AssignmentService
    authorizations: AuthorizationProjectionService


class AccessApplicationBuilder:
    """Build access services over one database transaction."""

    def __init__(
        self,
        *,
        digests: DigestService,
        countries: CountryReader,
        audit_writer: AuditWriter,
        cache_backend: CacheBackend,
        clock: Callable[[], datetime],
        allow_noop_audit: bool = False,
    ) -> None:
        """Initialize reusable access-control adapters."""
        if isinstance(audit_writer, NoOpAuditWriter) and not allow_noop_audit:
            raise ValueError(
                "Assignment management requires a transactional audit writer."
            )
        self.digests = digests
        self.countries = countries
        self.audit_writer = audit_writer
        self.cache_backend = cache_backend
        self.clock = clock

    def __call__(self, db: Session) -> AccessApplication:
        """Build services sharing the supplied database transaction."""
        repository = AssignmentRepository(db)
        cache = AuthorizationCache(
            backend=self.cache_backend,
            clock=self.clock,
        )
        evaluator = PolicyEvaluator(self.clock)
        grants = AuthGrantService(
            repository=AuthGrantRepository(db),
            digests=self.digests,
            clock=self.clock,
        )
        assignments = AssignmentService(
            repository=repository,
            accounts=AccountRepository(db),
            grants=grants,
            countries=self.countries,
            policy_evaluator=evaluator,
            audit_writer=self.audit_writer,
            transaction=DatabaseTransaction(db),
            cache=cache,
            clock=self.clock,
        )
        authorizations = AuthorizationProjectionService(
            repository=repository,
            cache=cache,
            evaluator=evaluator,
            clock=self.clock,
        )
        return AccessApplication(assignments, authorizations)
