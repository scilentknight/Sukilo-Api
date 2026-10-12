"""CLI for dry-run or policy-approved IAM retention of one country and data class.

The default mode is a dry run that only counts eligible rows. ``--execute``
re-runs the dry run, prints its count, and proceeds only when
``--confirm-count`` equals that count. A missing or unusable policy retains all
data, raises an alert line on stderr, and exits non-zero.

Policy source is a reviewed JSON file (``--policy-file``) with ``countries``,
``policies``, and ``holds`` entries. The job connects only through
``IAM_JOBS_DATABASE_URL`` and never falls back to the runtime database URL.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import _bootstrap  # noqa: F401

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.transactions import DatabaseTransaction
from app.modules.customers.retention import (
    DATA_CLASS_CUSTOMER_ADDRESS,
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_BATCHES,
    InMemoryRetentionObserver,
    RetentionAddressStore,
    RetentionError,
    RetentionEvidence,
    RetentionRequest,
    RetentionService,
    RetentionStatus,
    SqlRetentionAddressStore,
    StaticRetentionPolicyReader,
    build_retention_context,
    drain_retention,
)
from app.ports.audit import AuditWriter, NoOpAuditWriter

EXIT_OK = 0
EXIT_MISSING_POLICY = 2
EXIT_CONFIG = 3
EXIT_CONFIRMATION = 4
EXIT_FAILURE = 5


def _build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Process IAM data retention.")
    parser.add_argument("--country", required=True, help="ISO country code.")
    parser.add_argument(
        "--data-class",
        required=True,
        choices=[DATA_CLASS_CUSTOMER_ADDRESS],
    )
    parser.add_argument("--policy-file", type=Path, help="Reviewed policy JSON.")
    parser.add_argument("--execute", action="store_true", help="Apply changes.")
    parser.add_argument(
        "--confirm-count",
        type=int,
        help="Eligible count reported by the preceding dry run.",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-batches", type=int, default=DEFAULT_MAX_BATCHES)
    parser.add_argument("--after-address-id", type=UUID, help="Resume cursor.")
    return parser


def _load_reader(path: Path | None) -> StaticRetentionPolicyReader | None:
    """Load the reviewed policy file, or return None when unavailable."""
    if path is None or not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return StaticRetentionPolicyReader.from_document(document)
    except (OSError, ValueError, RetentionError):
        return None


def _jobs_session_factory() -> Callable[[], Session] | None:
    """Return a session factory bound to the dedicated jobs role, if set."""
    url = os.environ.get("IAM_JOBS_DATABASE_URL")
    if not url or url == settings.DATABASE_URL:
        return None
    return sessionmaker(bind=create_engine(url), autoflush=False, autocommit=False)


def _evidence_dict(evidence: RetentionEvidence) -> dict[str, object]:
    """Return aggregate evidence as a JSON-safe mapping without PII."""
    values = asdict(evidence)
    return {key: None if value is None else str(value) for key, value in values.items()}


def _print_evidence(label: str, evidence: RetentionEvidence) -> None:
    """Print one aggregate evidence line."""
    print(json.dumps({"phase": label, **_evidence_dict(evidence)}, sort_keys=True))


def _retained(observer: InMemoryRetentionObserver) -> int:
    """Report retained-data alerts on stderr and return the exit code."""
    for code, country_id, data_class, reason in observer.alerts:
        alert = {"alert": code, "country_id": str(country_id), "data_class": data_class}
        print(json.dumps({**alert, "reason": reason}), file=sys.stderr)
    print("Retention policy unavailable: all data retained.", file=sys.stderr)
    return EXIT_MISSING_POLICY


def _process(
    args: argparse.Namespace,
    country_id: UUID,
    reader: StaticRetentionPolicyReader,
    session: Session,
    store: RetentionAddressStore,
    audit_writer: AuditWriter,
) -> int:
    """Run the dry run and, when confirmed, the destructive run."""
    observer = InMemoryRetentionObserver()
    service = RetentionService(
        policies=reader,
        store=store,
        audit_writer=audit_writer,
        transaction=DatabaseTransaction(session),
        observer=observer,
        clock=lambda: datetime.now(UTC),
    )
    correlation_id = uuid4()
    context = build_retention_context(country_id, correlation_id)
    request = RetentionRequest(
        country_id=country_id,
        data_class=args.data_class,
        correlation_id=correlation_id,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        after_address_id=args.after_address_id,
    )
    dry = drain_retention(service, request, context, finish_unit=session.rollback)
    _print_evidence("dry_run", dry)
    if dry.status is RetentionStatus.RETAINED_POLICY_UNAVAILABLE:
        return _retained(observer)
    if not args.execute:
        return EXIT_OK
    if args.confirm_count != dry.eligible:
        print("--confirm-count must equal the dry-run eligible count.", file=sys.stderr)
        return EXIT_CONFIRMATION
    if isinstance(audit_writer, NoOpAuditWriter) and settings.APP_ENV.casefold() == "production":
        print("A real audit writer is required to execute in production.", file=sys.stderr)
        return EXIT_CONFIG
    executed = drain_retention(
        service,
        _execute_request(request),
        context,
        finish_unit=session.commit,
    )
    _print_evidence("execute", executed)
    if executed.status is RetentionStatus.RETAINED_POLICY_UNAVAILABLE:
        return _retained(observer)
    return EXIT_OK


def _execute_request(request: RetentionRequest) -> RetentionRequest:
    """Return the same bounded request with destructive execution enabled."""
    return RetentionRequest(
        country_id=request.country_id,
        data_class=request.data_class,
        correlation_id=request.correlation_id,
        execute=True,
        batch_size=request.batch_size,
        max_batches=request.max_batches,
        after_address_id=request.after_address_id,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
    store_factory: Callable[[Session], RetentionAddressStore] = SqlRetentionAddressStore,
    audit_writer: AuditWriter | None = None,
) -> int:
    """Run the retention CLI.

    Args:
        argv: Optional argument list; defaults to ``sys.argv``.
        session_factory: Optional job-role session factory for tests.
        store_factory: Builds the address store for a session.
        audit_writer: Optional audit writer; defaults to the no-op writer.

    Returns:
        Process exit code; non-zero when data was retained or a guard failed.
    """
    args = _build_parser().parse_args(argv)
    reader = _load_reader(args.policy_file)
    country_id = None if reader is None else reader.resolve_country_id(args.country)
    if reader is None or country_id is None:
        print("Retention policy unavailable: all data retained.", file=sys.stderr)
        return EXIT_MISSING_POLICY
    factory = session_factory or _jobs_session_factory()
    if factory is None:
        print("IAM_JOBS_DATABASE_URL must name the dedicated jobs role.", file=sys.stderr)
        return EXIT_CONFIG
    session = factory()
    try:
        return _process(
            args, country_id, reader, session, store_factory(session),
            audit_writer or NoOpAuditWriter(),
        )
    except (RetentionError, SQLAlchemyError) as exc:
        session.rollback()
        print(f"Retention failed: {type(exc).__name__}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
