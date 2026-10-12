"""Process policy-approved customer-address retention in bounded batches.

The job only touches soft-deleted address rows older than the country's
approved retention window. "Deletion" means clearing nullable approved fields
and replacing required approved fields with fixed placeholders, because the
retention job role has no hard-delete privilege. A missing policy always means
retain the data and raise an alert, never delete.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database_context import (
    JOB_PURPOSE_RETENTION,
    AuthorizationMode,
    DatabaseContextError,
    DatabaseRequestContext,
    apply_database_context,
    validate_database_context,
)
from app.core.transactions import DatabaseTransaction
from app.modules.customers.models import Address
from app.ports.audit import AuditEventInput, AuditWriter

DATA_CLASS_CUSTOMER_ADDRESS = "CUSTOMER_ADDRESS"
SUPPORTED_DATA_CLASSES = frozenset({DATA_CLASS_CUSTOMER_ADDRESS})
RETENTION_JOB_PRINCIPAL_ID = uuid5(NAMESPACE_URL, "urn:sukilo:iam:retention-job")
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 500
DEFAULT_MAX_BATCHES = 10
DEFAULT_MAX_RUNS = 1000
ALERT_POLICY_MISSING = "iam.retention.policy_missing"
ALERT_POLICY_REJECTED = "iam.retention.policy_rejected"
ALERT_JOB_FAILED = "iam.retention.job_failed"
AUDIT_ACTION_ADDRESS_RETAINED = "iam.retention.address_pseudonymized"
ADDRESS_FIELD_TARGETS: Mapping[str, object] = MappingProxyType(
    {
        "recipient_name": "Closed customer",
        "phone_e164": "closed",
        "address_line_1": "redacted",
        "address_line_2": None,
        "landmark": None,
        "city_text": None,
        "postal_code": None,
        "geography_area_id": None,
        "latitude": None,
        "longitude": None,
    }
)


class RetentionError(Exception):
    """Report a safe retention-job failure."""


class RetentionContextError(RetentionError):
    """Report a missing or incorrect database job context."""


class HoldKind(StrEnum):
    """Identify holds that block retention processing."""

    LEGAL = "LEGAL"
    FINANCE = "FINANCE"
    FRAUD = "FRAUD"


class RetentionStatus(StrEnum):
    """Describe how one retention run ended."""

    DRY_RUN = "DRY_RUN"
    EXECUTED = "EXECUTED"
    RETAINED_POLICY_UNAVAILABLE = "RETAINED_POLICY_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Describe one effective retention policy for a country and data class."""

    country_id: UUID
    data_class: str
    policy_version: str
    retention_days: int
    approved_fields: frozenset[str]
    approved: bool = False
    change_ticket: str | None = None


@dataclass(frozen=True, slots=True)
class MissingRetentionPolicy:
    """Signal that no usable policy exists, so data must be retained."""

    country_id: UUID
    data_class: str
    reason: str = "POLICY_MISSING"
    retain: bool = True


class RetentionPolicyReader(Protocol):
    """Read effective retention policy and hold state."""

    def get_policy(
        self,
        country_id: UUID,
        data_class: str,
    ) -> RetentionPolicy | MissingRetentionPolicy:
        """Return the effective policy or a retain-and-alert result."""
        ...

    def hold_kinds(self, user_id: UUID, country_id: UUID) -> frozenset[HoldKind]:
        """Return legal, finance, or fraud holds on one customer scope."""
        ...


class RetentionAddressStore(Protocol):
    """Select candidate address rows in stable, bounded batches."""

    def select_batch(
        self,
        country_id: UUID,
        cutoff: datetime,
        after_address_id: UUID | None,
        limit: int,
        *,
        for_update: bool,
    ) -> list[Address]:
        """Return soft-deleted rows older than the cutoff after the cursor."""
        ...


class RetentionObserver(Protocol):
    """Receive retention alerts and aggregate evidence without raw PII."""

    def record_alert(
        self,
        code: str,
        country_id: UUID,
        data_class: str,
        reason: str,
    ) -> None:
        """Record one retention alert."""
        ...

    def record_evidence(self, evidence: "RetentionEvidence") -> None:
        """Record aggregate evidence for one run."""
        ...


@dataclass(frozen=True, slots=True)
class RetentionRequest:
    """Describe one bounded retention run for one country and data class."""

    country_id: UUID
    data_class: str
    correlation_id: UUID
    execute: bool = False
    batch_size: int = DEFAULT_BATCH_SIZE
    max_batches: int = DEFAULT_MAX_BATCHES
    after_address_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RetentionEvidence:
    """Carry aggregate counts and cursor state only, never customer values."""

    status: RetentionStatus
    country_id: UUID
    data_class: str
    policy_version: str | None
    dry_run: bool
    scanned: int = 0
    eligible: int = 0
    held: int = 0
    already_done: int = 0
    changed: int = 0
    batches: int = 0
    next_cursor: UUID | None = None
    reason: str | None = None


@dataclass(slots=True)
class _RunState:
    """Track mutable counters and caches for one run."""

    request: RetentionRequest
    policy: RetentionPolicy
    now: datetime
    holds: dict[tuple[UUID, UUID], bool] = field(default_factory=dict)
    scanned: int = 0
    eligible: int = 0
    held: int = 0
    already_done: int = 0
    changed: int = 0
    batches: int = 0


class InMemoryRetentionObserver:
    """Collect alerts and evidence in process for tests and the CLI."""

    def __init__(self) -> None:
        """Initialize empty alert and evidence lists."""
        self.alerts: list[tuple[str, UUID, str, str]] = []
        self.evidence: list[RetentionEvidence] = []

    def record_alert(
        self,
        code: str,
        country_id: UUID,
        data_class: str,
        reason: str,
    ) -> None:
        """Record one alert tuple."""
        self.alerts.append((code, country_id, data_class, reason))

    def record_evidence(self, evidence: RetentionEvidence) -> None:
        """Record one aggregate evidence object."""
        self.evidence.append(evidence)


class StaticRetentionPolicyReader:
    """Serve reviewed policies and holds from in-memory configuration."""

    def __init__(
        self,
        *,
        policies: Sequence[RetentionPolicy] = (),
        holds: Mapping[tuple[UUID, UUID], frozenset[HoldKind]] | None = None,
        countries: Mapping[str, UUID] | None = None,
    ) -> None:
        """Initialize policies, holds, and the country-code map."""
        self._policies = {(item.country_id, item.data_class): item for item in policies}
        self._holds = dict(holds or {})
        self._countries = dict(countries or {})

    def resolve_country_id(self, code: str) -> UUID | None:
        """Return the configured country ID for a code, if any."""
        return self._countries.get(code.upper())

    def get_policy(
        self,
        country_id: UUID,
        data_class: str,
    ) -> RetentionPolicy | MissingRetentionPolicy:
        """Return the configured policy or a retain result."""
        policy = self._policies.get((country_id, data_class))
        if policy is None:
            return MissingRetentionPolicy(country_id, data_class)
        return policy

    def hold_kinds(self, user_id: UUID, country_id: UUID) -> frozenset[HoldKind]:
        """Return configured holds for one customer scope."""
        return self._holds.get((user_id, country_id), frozenset())

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> "StaticRetentionPolicyReader":
        """Build a reader from a reviewed JSON-style policy document.

        Args:
            document: Mapping with optional ``countries``, ``policies``, and
                ``holds`` entries.

        Returns:
            A reader serving the configured values.

        Raises:
            RetentionError: If an entry is malformed.
        """
        try:
            countries = {
                str(code).upper(): UUID(str(value))
                for code, value in dict(document.get("countries", {})).items()
            }
            policies = [_parse_policy(entry) for entry in document.get("policies", [])]
            holds = {
                (UUID(str(entry["user_id"])), UUID(str(entry["country_id"]))): frozenset(
                    HoldKind(kind) for kind in entry["kinds"]
                )
                for entry in document.get("holds", [])
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise RetentionError("Retention policy document is malformed.") from exc
        return cls(policies=policies, holds=holds, countries=countries)


def _parse_policy(entry: Mapping[str, object]) -> RetentionPolicy:
    """Parse one policy entry from a reviewed document."""
    return RetentionPolicy(
        country_id=UUID(str(entry["country_id"])),
        data_class=str(entry["data_class"]),
        policy_version=str(entry["policy_version"]),
        retention_days=int(str(entry["retention_days"])),
        approved_fields=frozenset(str(item) for item in entry["approved_fields"]),
        approved=bool(entry.get("approved", False)),
        change_ticket=(
            str(entry["change_ticket"]) if entry.get("change_ticket") else None
        ),
    )


class SqlRetentionAddressStore:
    """Select address retention candidates with parameterized SQL."""

    def __init__(self, db: Session) -> None:
        """Initialize the store with a job-role session."""
        self.db = db

    def select_batch(
        self,
        country_id: UUID,
        cutoff: datetime,
        after_address_id: UUID | None,
        limit: int,
        *,
        for_update: bool,
    ) -> list[Address]:
        """Return soft-deleted rows older than the cutoff, ordered by ID."""
        statement = select(Address).where(
            Address.country_id == country_id,
            Address.deleted_at.is_not(None),
            Address.deleted_at <= cutoff,
        )
        if after_address_id is not None:
            statement = statement.where(Address.address_id > after_address_id)
        statement = statement.order_by(Address.address_id).limit(limit)
        if for_update:
            statement = statement.with_for_update()
        return list(self.db.scalars(statement))


def build_retention_context(
    country_id: UUID,
    correlation_id: UUID,
    effective_user_id: UUID = RETENTION_JOB_PRINCIPAL_ID,
) -> DatabaseRequestContext:
    """Build the narrow SYSTEM_JOB context a retention run requires.

    Args:
        country_id: Explicit target country.
        correlation_id: Correlation ID for the run.
        effective_user_id: Job principal recorded as the effective user.

    Returns:
        A trusted retention database context.
    """
    return DatabaseRequestContext(
        authorization_mode=AuthorizationMode.SYSTEM_JOB,
        actor_user_id=effective_user_id,
        effective_user_id=effective_user_id,
        effective_country_id=country_id,
        job_purpose=JOB_PURPOSE_RETENTION,
        correlation_id=correlation_id,
    )


class RetentionService:
    """Run dry-run or destructive retention for one country and data class."""

    def __init__(
        self,
        *,
        policies: RetentionPolicyReader,
        store: RetentionAddressStore,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        observer: RetentionObserver,
        clock: Callable[[], datetime],
        context_applier: Callable[
            [Session, DatabaseRequestContext],
            None,
        ] = apply_database_context,
    ) -> None:
        """Initialize retention dependencies."""
        self.policies = policies
        self.store = store
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.observer = observer
        self.clock = clock
        self.context_applier = context_applier

    def run(
        self,
        request: RetentionRequest,
        context: DatabaseRequestContext | None,
    ) -> RetentionEvidence:
        """Run one bounded retention unit inside the caller's transaction.

        Args:
            request: Explicit country, data class, and batch bounds.
            context: Trusted SYSTEM_JOB/RETENTION context for the same country.

        Returns:
            Aggregate evidence for the run, including the resume cursor.

        Raises:
            RetentionContextError: If the required job context is absent.
            RetentionError: If the request or scanned rows are invalid.
        """
        self._require_context(request, context)
        _validate_request(request)
        policy = self.policies.get_policy(request.country_id, request.data_class)
        problem = _policy_problem(policy, request)
        if problem is not None or not isinstance(policy, RetentionPolicy):
            return self._retain(request, problem or "POLICY_MISSING")
        self.context_applier(self.transaction.session, context)
        try:
            evidence = self._scan(_RunState(request, policy, self.clock()))
        except (RetentionError, SQLAlchemyError):
            self.observer.record_alert(
                ALERT_JOB_FAILED,
                request.country_id,
                request.data_class,
                "JOB_FAILED",
            )
            raise
        self.observer.record_evidence(evidence)
        return evidence

    def _require_context(
        self,
        request: RetentionRequest,
        context: DatabaseRequestContext | None,
    ) -> None:
        """Refuse to run without a matching retention job context."""
        if context is None:
            raise RetentionContextError("A retention database context is required.")
        try:
            validate_database_context(context)
        except DatabaseContextError as exc:
            raise RetentionContextError("Retention database context is invalid.") from exc
        if (
            context.authorization_mode is not AuthorizationMode.SYSTEM_JOB
            or context.job_purpose != JOB_PURPOSE_RETENTION
            or context.effective_user_id is None
            or context.effective_country_id != request.country_id
        ):
            raise RetentionContextError("Retention database context does not match.")

    def _retain(self, request: RetentionRequest, reason: str) -> RetentionEvidence:
        """Alert and report a retain-only result."""
        code = ALERT_POLICY_MISSING if reason == "POLICY_MISSING" else ALERT_POLICY_REJECTED
        self.observer.record_alert(code, request.country_id, request.data_class, reason)
        evidence = RetentionEvidence(
            status=RetentionStatus.RETAINED_POLICY_UNAVAILABLE,
            country_id=request.country_id,
            data_class=request.data_class,
            policy_version=None,
            dry_run=not request.execute,
            reason=reason,
        )
        self.observer.record_evidence(evidence)
        return evidence

    def _scan(self, state: _RunState) -> RetentionEvidence:
        """Process up to the batch bound and return aggregate evidence."""
        request = state.request
        cutoff = state.now - timedelta(days=state.policy.retention_days)
        cursor = request.after_address_id
        next_cursor: UUID | None = cursor
        for _ in range(request.max_batches):
            batch = self.store.select_batch(
                request.country_id,
                cutoff,
                cursor,
                request.batch_size,
                for_update=request.execute,
            )
            state.batches += 1 if batch else 0
            for address in batch:
                self._handle(address, state)
            if len(batch) < request.batch_size:
                next_cursor = None
                break
            cursor = batch[-1].address_id
            next_cursor = cursor
        return RetentionEvidence(
            status=RetentionStatus.EXECUTED if request.execute else RetentionStatus.DRY_RUN,
            country_id=request.country_id,
            data_class=request.data_class,
            policy_version=state.policy.policy_version,
            dry_run=not request.execute,
            scanned=state.scanned,
            eligible=state.eligible,
            held=state.held,
            already_done=state.already_done,
            changed=state.changed,
            batches=state.batches,
            next_cursor=next_cursor,
        )

    def _handle(self, address: Address, state: _RunState) -> None:
        """Classify one candidate and apply the policy when executing."""
        if address.country_id != state.request.country_id:
            raise RetentionError("Retention candidate is outside the job country.")
        state.scanned += 1
        if self._is_held(address, state):
            state.held += 1
            return
        fields = state.policy.approved_fields
        if _already_applied(address, fields):
            state.already_done += 1
            return
        state.eligible += 1
        if not state.request.execute:
            return
        _apply_fields(address, fields, state.now)
        state.changed += 1
        self._audit(address, state)

    def _is_held(self, address: Address, state: _RunState) -> bool:
        """Return whether any legal, finance, or fraud hold applies."""
        key = (address.user_id, address.country_id)
        if key not in state.holds:
            kinds = self.policies.hold_kinds(address.user_id, address.country_id)
            state.holds[key] = bool(kinds)
        return state.holds[key]

    def _audit(self, address: Address, state: _RunState) -> None:
        """Stage one per-resource audit reference without field values."""
        self.audit_writer.stage(
            AuditEventInput(
                action=AUDIT_ACTION_ADDRESS_RETAINED,
                resource_type="address",
                resource_id=address.address_id,
                correlation_id=state.request.correlation_id,
                actor_user_id=None,
                effective_user_id=address.user_id,
                country_id=address.country_id,
                after_data={
                    "data_class": state.request.data_class,
                    "policy_version": state.policy.policy_version,
                    "field_count": len(state.policy.approved_fields),
                },
            ),
            self.transaction,
        )


def _validate_request(request: RetentionRequest) -> None:
    """Reject unsupported data classes and unbounded batch settings."""
    if request.data_class not in SUPPORTED_DATA_CLASSES:
        raise RetentionError("Retention data class is unsupported.")
    if not 1 <= request.batch_size <= MAX_BATCH_SIZE:
        raise RetentionError("Retention batch size is out of bounds.")
    if request.max_batches < 1:
        raise RetentionError("Retention batch limit must be positive.")


def _policy_problem(
    policy: RetentionPolicy | MissingRetentionPolicy,
    request: RetentionRequest,
) -> str | None:
    """Return a retain reason when the policy cannot be used, else None."""
    if isinstance(policy, MissingRetentionPolicy):
        return policy.reason
    if (
        policy.country_id != request.country_id
        or policy.data_class != request.data_class
        or policy.retention_days < 1
        or not policy.approved_fields
        or not policy.approved_fields <= ADDRESS_FIELD_TARGETS.keys()
    ):
        return "POLICY_INVALID"
    if request.execute and (not policy.approved or not policy.change_ticket):
        return "POLICY_NOT_APPROVED"
    return None


def _already_applied(address: Address, fields: frozenset[str]) -> bool:
    """Return whether every approved field already holds its target value."""
    return all(getattr(address, name) == ADDRESS_FIELD_TARGETS[name] for name in fields)


def _apply_fields(address: Address, fields: frozenset[str], now: datetime) -> None:
    """Set approved fields to their targets and bump the row version."""
    for name in sorted(fields):
        setattr(address, name, ADDRESS_FIELD_TARGETS[name])
    address.updated_at = now
    address.version += 1


def combine_evidence(parts: Sequence[RetentionEvidence]) -> RetentionEvidence:
    """Merge per-run evidence into one aggregate.

    Args:
        parts: Non-empty evidence from consecutive runs.

    Returns:
        Summed counts with the last run's status and cursor.

    Raises:
        ValueError: If no evidence is supplied.
    """
    if not parts:
        raise ValueError("At least one evidence item is required.")
    last = parts[-1]
    return replace(
        last,
        scanned=sum(item.scanned for item in parts),
        eligible=sum(item.eligible for item in parts),
        held=sum(item.held for item in parts),
        already_done=sum(item.already_done for item in parts),
        changed=sum(item.changed for item in parts),
        batches=sum(item.batches for item in parts),
    )


def drain_retention(
    service: RetentionService,
    request: RetentionRequest,
    context: DatabaseRequestContext | None,
    *,
    finish_unit: Callable[[], None],
    max_runs: int = DEFAULT_MAX_RUNS,
) -> RetentionEvidence:
    """Run consecutive bounded units until the cursor is exhausted.

    Args:
        service: Retention service to run.
        request: First request; later runs resume from the returned cursor.
        context: Trusted retention context passed to every run.
        finish_unit: Commit (execute) or roll back (dry-run) one unit.
        max_runs: Upper bound on units in this call.

    Returns:
        Aggregate evidence; ``next_cursor`` is set when work remains.
    """
    parts: list[RetentionEvidence] = []
    current = request
    for _ in range(max_runs):
        evidence = service.run(current, context)
        finish_unit()
        parts.append(evidence)
        if (
            evidence.next_cursor is None
            or evidence.status is RetentionStatus.RETAINED_POLICY_UNAVAILABLE
        ):
            break
        current = replace(current, after_address_id=evidence.next_cursor)
    return combine_evidence(parts)
