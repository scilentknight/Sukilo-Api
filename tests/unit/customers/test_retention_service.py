"""Test policy-approved, bounded, idempotent customer retention."""

import importlib.util
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.core.database_context import AuthorizationMode, DatabaseRequestContext
from app.core.transactions import DatabaseTransaction
from app.modules.customers.models import Address
from app.modules.customers.retention import (
    ALERT_POLICY_MISSING,
    ALERT_POLICY_REJECTED,
    DATA_CLASS_CUSTOMER_ADDRESS,
    HoldKind,
    InMemoryRetentionObserver,
    RetentionContextError,
    RetentionError,
    RetentionPolicy,
    RetentionRequest,
    RetentionService,
    RetentionStatus,
    SqlRetentionAddressStore,
    StaticRetentionPolicyReader,
    build_retention_context,
    drain_retention,
)
from app.modules.customers.types import AddressType
from tests.fakes.iam import RecordingAuditWriter

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=400)
COUNTRY_ID = UUID(int=100)
OTHER_COUNTRY_ID = UUID(int=101)
CORRELATION_ID = UUID(int=7)
ALL_FIELDS = frozenset(
    {"recipient_name", "phone_e164", "address_line_1", "address_line_2", "city_text"}
)
RAW_VALUES = ("Asha Rai", "9841234567", "12 Lake Road", "Pokhara")


def _address(number: int, **overrides: object) -> Address:
    values: dict[str, object] = {
        "address_id": UUID(int=number),
        "user_id": UUID(int=1000 + number),
        "country_id": COUNTRY_ID,
        "recipient_name": "Asha Rai",
        "phone_e164": "+9779841234567",
        "address_line_1": "12 Lake Road",
        "address_line_2": "Apt 4",
        "city_text": "Pokhara",
        "address_type": AddressType.HOME,
        "deleted_at": OLD,
        "version": 1,
        "is_default_shipping": False,
        "is_default_billing": False,
    }
    values.update(overrides)
    return Address(**values)


def _policy(**overrides: object) -> RetentionPolicy:
    values: dict[str, object] = {
        "country_id": COUNTRY_ID,
        "data_class": DATA_CLASS_CUSTOMER_ADDRESS,
        "policy_version": "np-2026-1",
        "retention_days": 365,
        "approved_fields": ALL_FIELDS,
        "approved": True,
        "change_ticket": "CHG-1",
    }
    values.update(overrides)
    return RetentionPolicy(**values)


class _Store:
    def __init__(self, rows: list[Address]) -> None:
        self.rows = rows
        self.calls: list[tuple[UUID | None, int, bool]] = []

    def select_batch(
        self,
        country_id: UUID,
        cutoff: datetime,
        after_address_id: UUID | None,
        limit: int,
        *,
        for_update: bool,
    ) -> list[Address]:
        self.calls.append((after_address_id, limit, for_update))
        matches = [
            row
            for row in self.rows
            if row.country_id == country_id
            and row.deleted_at is not None
            and row.deleted_at <= cutoff
            and (after_address_id is None or row.address_id > after_address_id)
        ]
        return sorted(matches, key=lambda row: row.address_id)[:limit]


def _build(
    rows: list[Address],
    policy: RetentionPolicy | None = None,
    holds: dict[tuple[UUID, UUID], frozenset[HoldKind]] | None = None,
) -> tuple[RetentionService, _Store, InMemoryRetentionObserver, RecordingAuditWriter, list]:
    store = _Store(rows)
    observer = InMemoryRetentionObserver()
    audit = RecordingAuditWriter()
    applied: list[DatabaseRequestContext] = []
    reader = StaticRetentionPolicyReader(
        policies=[] if policy is None else [policy],
        holds=holds,
    )
    service = RetentionService(
        policies=reader,
        store=store,
        audit_writer=audit,
        transaction=DatabaseTransaction(SimpleNamespace()),
        observer=observer,
        clock=lambda: NOW,
        context_applier=lambda session, context: applied.append(context),
    )
    return service, store, observer, audit, applied


def _request(**overrides: object) -> RetentionRequest:
    values: dict[str, object] = {
        "country_id": COUNTRY_ID,
        "data_class": DATA_CLASS_CUSTOMER_ADDRESS,
        "correlation_id": CORRELATION_ID,
    }
    values.update(overrides)
    return RetentionRequest(**values)


def _context() -> DatabaseRequestContext:
    return build_retention_context(COUNTRY_ID, CORRELATION_ID)


def test_missing_policy_retains_data_and_records_alert() -> None:
    """Retain every row and alert when no policy exists."""
    rows = [_address(1)]
    service, store, observer, audit, applied = _build(rows)
    evidence = service.run(_request(execute=True), _context())
    assert evidence.status is RetentionStatus.RETAINED_POLICY_UNAVAILABLE
    assert evidence.changed == 0
    assert rows[0].address_line_1 == "12 Lake Road"
    assert store.calls == [] and applied == [] and audit.records == []
    assert observer.alerts[0][0] == ALERT_POLICY_MISSING


def test_invalid_or_unapproved_policy_is_retained_with_alert() -> None:
    """Treat unknown fields and unapproved execution as retain-and-alert."""
    rows = [_address(1)]
    bad_fields = _policy(approved_fields=frozenset({"password_hash"}))
    service, _, observer, _, _ = _build(rows, bad_fields)
    assert service.run(_request(), _context()).changed == 0
    assert observer.alerts[0][0] == ALERT_POLICY_REJECTED
    unapproved = _policy(approved=False)
    service, _, observer, _, _ = _build(rows, unapproved)
    evidence = service.run(_request(execute=True), _context())
    assert evidence.status is RetentionStatus.RETAINED_POLICY_UNAVAILABLE
    assert rows[0].recipient_name == "Asha Rai"
    assert service.run(_request(execute=False), _context()).eligible == 1


def test_run_refuses_without_or_with_wrong_context() -> None:
    """Refuse missing, wrong-mode, wrong-purpose, and wrong-country contexts."""
    rows = [_address(1)]
    service, store, _, _, applied = _build(rows, _policy())
    wrong = [
        None,
        replace(_context(), authorization_mode=AuthorizationMode.SELF),
        replace(_context(), job_purpose="CUSTOMER_EXPORT"),
        replace(_context(), effective_country_id=OTHER_COUNTRY_ID),
        replace(_context(), effective_user_id=None),
    ]
    for context in wrong:
        with pytest.raises(RetentionContextError):
            service.run(_request(execute=True), context)
    assert store.calls == [] and applied == []
    assert rows[0].address_line_1 == "12 Lake Road"


def test_job_context_is_system_retention_with_explicit_scope() -> None:
    """Apply the SYSTEM_JOB context with explicit user and country."""
    service, _, _, _, applied = _build([_address(1)], _policy())
    service.run(_request(), _context())
    context = applied[0]
    assert context.authorization_mode is AuthorizationMode.SYSTEM_JOB
    assert context.job_purpose == "RETENTION"
    assert context.effective_country_id == COUNTRY_ID
    assert context.effective_user_id is not None


def test_dry_run_counts_without_mutation_or_audit() -> None:
    """Count eligible rows and leave every value untouched."""
    rows = [_address(1), _address(2), _address(3, deleted_at=None)]
    service, store, _, audit, _ = _build(rows, _policy())
    evidence = service.run(_request(), _context())
    assert evidence.status is RetentionStatus.DRY_RUN
    assert (evidence.eligible, evidence.changed) == (2, 0)
    assert all(call[2] is False for call in store.calls)
    assert audit.records == [] and rows[0].version == 1


def test_recent_deletions_and_active_rows_are_not_selected() -> None:
    """Skip rows inside the retention window and active rows."""
    rows = [_address(1, deleted_at=NOW - timedelta(days=10)), _address(2, deleted_at=None)]
    service, *_ = _build(rows, _policy())
    assert service.run(_request(execute=True), _context()).scanned == 0


def test_bounded_batches_resume_from_stable_cursor() -> None:
    """Process a bounded unit and resume from the returned cursor."""
    rows = [_address(number) for number in range(1, 6)]
    service, store, _, _, _ = _build(rows, _policy())
    first = service.run(_request(execute=True, batch_size=2, max_batches=1), _context())
    assert (first.changed, first.next_cursor) == (2, UUID(int=2))
    second = service.run(
        _request(execute=True, batch_size=2, max_batches=1, after_address_id=first.next_cursor),
        _context(),
    )
    assert (second.changed, second.next_cursor) == (2, UUID(int=4))
    last = service.run(
        _request(execute=True, batch_size=2, max_batches=5, after_address_id=second.next_cursor),
        _context(),
    )
    assert (last.changed, last.next_cursor) == (1, None)
    assert all(call[1] == 2 for call in store.calls)


def test_batch_size_and_batch_limit_are_bounded() -> None:
    """Reject unbounded batches and unsupported data classes."""
    service, *_ = _build([], _policy())
    for bad in (
        _request(batch_size=0),
        _request(batch_size=10_000),
        _request(max_batches=0),
        _request(data_class="ORDERS"),
    ):
        with pytest.raises(RetentionError):
            service.run(bad, _context())


@pytest.mark.parametrize("kind", list(HoldKind))
def test_legal_finance_and_fraud_holds_are_skipped(kind: HoldKind) -> None:
    """Never touch a held customer, but process the others."""
    rows = [_address(1), _address(2)]
    holds = {(rows[0].user_id, COUNTRY_ID): frozenset({kind})}
    service, _, _, audit, _ = _build(rows, _policy(), holds)
    evidence = service.run(_request(execute=True), _context())
    assert (evidence.held, evidence.changed) == (1, 1)
    assert rows[0].recipient_name == "Asha Rai"
    assert rows[1].recipient_name == "Closed customer"
    assert [record[0].resource_id for record in audit.records] == [rows[1].address_id]


def test_execution_is_idempotent_and_only_touches_approved_fields() -> None:
    """Change approved fields once and leave other fields alone."""
    rows = [_address(1)]
    policy = _policy(approved_fields=frozenset({"address_line_1", "phone_e164", "city_text"}))
    service, _, _, audit, _ = _build(rows, policy)
    first = service.run(_request(execute=True), _context())
    second = service.run(_request(execute=True), _context())
    assert (first.changed, second.changed, second.already_done) == (1, 0, 1)
    address = rows[0]
    assert (address.address_line_1, address.phone_e164, address.city_text) == (
        "redacted",
        "closed",
        None,
    )
    assert address.recipient_name == "Asha Rai" and address.address_line_2 == "Apt 4"
    assert address.version == 2 and len(audit.records) == 1


def test_cross_country_candidate_is_rejected() -> None:
    """Fail closed if a store returns a row from another country."""
    service, store, observer, _, _ = _build([_address(1)], _policy())
    store.select_batch = lambda *args, **kwargs: [_address(1, country_id=OTHER_COUNTRY_ID)]
    with pytest.raises(RetentionError):
        service.run(_request(execute=True), _context())
    assert observer.alerts[-1][0] == "iam.retention.job_failed"


def test_evidence_and_audit_contain_no_raw_pii() -> None:
    """Keep aggregate evidence and audit payloads free of customer values."""
    rows = [_address(1)]
    service, _, observer, audit, _ = _build(rows, _policy())
    evidence = service.run(_request(execute=True), _context())
    serialized = repr([evidence, observer.evidence, audit.records]).casefold()
    for value in RAW_VALUES:
        assert value.casefold() not in serialized


def test_drain_runs_units_and_merges_evidence() -> None:
    """Commit each unit and merge counts across units."""
    rows = [_address(number) for number in range(1, 6)]
    service, _, _, _, _ = _build(rows, _policy())
    finished: list[int] = []
    evidence = drain_retention(
        service,
        _request(execute=True, batch_size=2, max_batches=1),
        _context(),
        finish_unit=lambda: finished.append(1),
    )
    assert (evidence.changed, evidence.scanned, evidence.next_cursor) == (5, 5, None)
    assert len(finished) == 3


class _Db:
    def __init__(self) -> None:
        self.captured: list[object] = []

    def scalars(self, statement: object) -> list[Address]:
        self.captured.append(statement)
        return []


def test_sql_store_selects_bounded_stable_country_scoped_rows() -> None:
    """Compile bounded, ordered, country-scoped SQL with row locking."""
    database = _Db()
    store = SqlRetentionAddressStore(database)  # type: ignore[arg-type]
    store.select_batch(COUNTRY_ID, NOW, UUID(int=5), 25, for_update=True)
    sql = str(database.captured[0].compile(dialect=postgresql.dialect())).casefold()  # type: ignore[attr-defined]
    assert "deleted_at is not null" in sql and "country_id =" in sql
    assert "address_id >" in sql and "order by iam.address.address_id" in sql
    assert "limit" in sql and "for update" in sql


def _load_cli(monkeypatch: pytest.MonkeyPatch) -> object:
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "process_iam_retention",
        scripts / "process_iam_retention.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Session:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def execute(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None


def _policy_file(tmp_path: Path, **policy_overrides: object) -> Path:
    policy = {
        "country_id": str(COUNTRY_ID),
        "data_class": DATA_CLASS_CUSTOMER_ADDRESS,
        "policy_version": "np-2026-1",
        "retention_days": 365,
        "approved_fields": sorted(ALL_FIELDS),
        "approved": True,
        "change_ticket": "CHG-1",
    }
    policy.update(policy_overrides)
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps({"countries": {"NP": str(COUNTRY_ID)}, "policies": [policy]}),
        encoding="utf-8",
    )
    return path


def _run_cli(
    module: object,
    args: list[str],
    rows: list[Address],
    session: _Session,
) -> int:
    return module.main(  # type: ignore[attr-defined]
        ["--country", "NP", "--data-class", DATA_CLASS_CUSTOMER_ADDRESS, *args],
        session_factory=lambda: session,
        store_factory=lambda _session: _Store(rows),
    )


def test_cli_missing_policy_exits_nonzero_without_touching_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Retain data and never open a session when the policy is missing."""
    module = _load_cli(monkeypatch)
    rows = [_address(1)]
    opened: list[int] = []
    code = module.main(  # type: ignore[attr-defined]
        ["--country", "NP", "--data-class", DATA_CLASS_CUSTOMER_ADDRESS,
         "--policy-file", str(tmp_path / "missing.json"), "--execute"],
        session_factory=lambda: opened.append(1),
    )
    assert code != 0 and opened == [] and rows[0].address_line_1 == "12 Lake Road"


def test_cli_default_is_dry_run_and_execute_needs_confirmed_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default to dry run, and require the dry-run count to execute."""
    module = _load_cli(monkeypatch)
    policy = str(_policy_file(tmp_path))
    rows = [_address(1), _address(2)]
    session = _Session()
    assert _run_cli(module, ["--policy-file", policy], rows, session) == 0
    assert session.commits == 0 and rows[0].address_line_1 == "12 Lake Road"
    assert '"eligible": "2"' in capsys.readouterr().out
    assert _run_cli(module, ["--policy-file", policy, "--execute"], rows, session) != 0
    assert _run_cli(
        module, ["--policy-file", policy, "--execute", "--confirm-count", "1"], rows, session
    ) != 0
    assert session.commits == 0 and rows[0].address_line_1 == "12 Lake Road"
    assert _run_cli(
        module, ["--policy-file", policy, "--execute", "--confirm-count", "2"], rows, session
    ) == 0
    assert session.commits >= 1 and rows[0].address_line_1 == "redacted"


def test_cli_unapproved_policy_never_executes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exit non-zero and keep data when execution lacks an approved policy."""
    module = _load_cli(monkeypatch)
    policy = str(_policy_file(tmp_path, approved=False))
    rows = [_address(1)]
    code = _run_cli(
        module, ["--policy-file", policy, "--execute", "--confirm-count", "1"], rows, _Session()
    )
    assert code != 0 and rows[0].address_line_1 == "12 Lake Road"
