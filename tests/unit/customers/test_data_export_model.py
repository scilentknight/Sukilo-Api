"""Test the customer data export request model metadata."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, Text, UniqueConstraint

from app import models as app_models
from app.modules.customers import models
from app.modules.customers.types import DataExportStatus


def test_data_export_request_matches_phase5_columns() -> None:
    """Require every column from the Phase 5 export contract."""
    table = models.DataExportRequest.__table__
    assert table.fullname == "iam.data_export_request"
    assert {column.name for column in table.columns} == {
        "export_request_id",
        "user_id",
        "country_id",
        "idempotency_key",
        "status",
        "artifact_reference",
        "artifact_expires_at",
        "requested_at",
        "started_at",
        "completed_at",
        "failed_at",
        "failure_code",
        "downloaded_at",
        "correlation_id",
        "version",
    }
    assert table.c.idempotency_key.type.length == 128
    assert table.c.status.type.length == 24
    assert table.c.failure_code.type.length == 64
    assert isinstance(table.c.artifact_reference.type, Text)
    assert table.c.artifact_reference.nullable is True
    assert table.c.artifact_expires_at.nullable is True
    assert isinstance(table.c.requested_at.type, DateTime)
    assert table.c.requested_at.nullable is False
    assert table.c.correlation_id.nullable is False
    assert table.c.version.nullable is False
    assert table.c.version.server_default is not None
    assert table.c.version.default.arg == 1


def test_data_export_statuses_match_spec() -> None:
    """Keep export lifecycle codes stable and constrained in the table."""
    expected = [
        "PENDING",
        "PROCESSING",
        "READY",
        "FAILED",
        "EXPIRED",
        "DOWNLOADED",
    ]
    assert [member.value for member in DataExportStatus] == expected
    table = models.DataExportRequest.__table__
    check = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_data_export_request_status"
    )
    for status in expected:
        assert f"'{status}'" in str(check.sqltext)


def test_data_export_request_constraints_and_indexes() -> None:
    """Require idempotency uniqueness, lookup indexes, and restrictive FK."""
    table = models.DataExportRequest.__table__
    unique = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    assert unique.name == "uq_data_export_request_user_country_idempotency"
    assert [column.name for column in unique.columns] == [
        "user_id",
        "country_id",
        "idempotency_key",
    ]
    indexes = {
        index.name: [column.name for column in index.columns]
        for index in table.indexes
    }
    assert indexes["ix_data_export_request_user_status"] == [
        "user_id",
        "status",
    ]
    assert indexes["ix_data_export_request_artifact_expires_at"] == [
        "artifact_expires_at"
    ]
    user_fk = next(
        foreign_key
        for foreign_key in table.foreign_keys
        if "user_account" in foreign_key.target_fullname
    )
    assert user_fk.parent.name == "user_id"
    assert user_fk.ondelete == "RESTRICT"


def test_data_export_request_repr_excludes_artifact_reference() -> None:
    """Keep artifact references and idempotency keys out of repr output."""
    request = models.DataExportRequest(
        export_request_id=uuid4(),
        user_id=uuid4(),
        country_id=uuid4(),
        idempotency_key="secret-idempotency-key",
        status=DataExportStatus.READY,
        artifact_reference="vault://exports/secret-artifact",
        artifact_expires_at=datetime(2026, 10, 12),
        correlation_id=uuid4(),
    )
    rendered = repr(request)
    assert "secret-artifact" not in rendered
    assert "secret-idempotency-key" not in rendered
    assert "artifact_reference" not in rendered
    assert "READY" in rendered


def test_data_export_request_is_exported_from_model_packages() -> None:
    """Expose the model to Alembic metadata and application imports."""
    assert app_models.DataExportRequest is models.DataExportRequest
    assert "DataExportRequest" in models.__all__
    assert "DataExportRequest" in app_models.__all__
