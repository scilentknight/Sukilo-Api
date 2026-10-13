"""Test Phase 3 access-control model metadata."""

from sqlalchemy import CheckConstraint

from app.modules.access import models


def test_role_matches_scope_and_lifecycle_contract() -> None:
    """Require stable role fields, constraints, and indexes."""
    table = models.Role.__table__
    assert table.fullname == "iam.role"
    assert {column.name for column in table.columns} == {
        "role_id",
        "code",
        "name",
        "scope_type",
        "status",
        "is_system",
        "created_at",
        "updated_at",
    }
    assert {"uq_role_code", "ix_role_status"}.issubset(
        {index.name for index in table.indexes}
    )


def test_permission_matches_catalog_contract() -> None:
    """Require immutable permission definition fields and indexes."""
    table = models.Permission.__table__
    assert table.fullname == "iam.permission"
    assert {column.name for column in table.columns} == {
        "permission_id",
        "code",
        "description",
        "status",
        "created_at",
    }
    assert {"uq_permission_code", "ix_permission_status"}.issubset(
        {index.name for index in table.indexes}
    )


def test_role_permission_uses_restricted_composite_key() -> None:
    """Require one restricted mapping per role and permission."""
    table = models.RolePermission.__table__
    assert [column.name for column in table.primary_key.columns] == [
        "role_id",
        "permission_id",
    ]
    assert all(
        foreign_key.ondelete == "RESTRICT"
        for foreign_key in table.foreign_keys
    )


def test_assignment_matches_scoped_revocation_contract() -> None:
    """Require scoped assignment fields, checks, and active indexes."""
    table = models.UserRoleAssignment.__table__
    assert table.fullname == "iam.user_role_assignment"
    assert {column.name for column in table.columns} == {
        "assignment_id",
        "user_id",
        "role_id",
        "scope_type",
        "scope_id",
        "country_id",
        "vendor_id",
        "valid_from",
        "valid_to",
        "created_by",
        "created_at",
        "revoked_at",
        "revoked_by",
        "revocation_reason",
        "version",
    }
    assert {
        "ix_assignment_user_active",
        "ix_assignment_country_user_active",
        "ix_assignment_vendor_user_active",
        "ix_assignment_role_scope_active",
    }.issubset({index.name for index in table.indexes})
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_assignment_validity_window",
        "ck_assignment_scope_shape",
    }.issubset(check_names)
    assert all(
        foreign_key.ondelete == "RESTRICT"
        for foreign_key in table.foreign_keys
    )
