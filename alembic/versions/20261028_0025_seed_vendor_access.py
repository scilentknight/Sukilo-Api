"""Seed vendor roles and application permissions.

Revision ID: 20261028_0025
Revises: 20261028_0024
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.modules.access.catalog import PERMISSIONS, ROLES, ROLE_PERMISSIONS

revision: str = "20261028_0025"
down_revision: str | Sequence[str] | None = "20261028_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_ROLE_CODES = ("VENDOR_OWNER", "VENDOR_STAFF")
NEW_PERMISSION_CODES = (
    "vendor.application.read",
    "vendor.application.submit",
    "vendor.application.review",
    "vendor.application.approve",
    "vendor.staff.invite",
    "vendor.staff.revoke",
)


def upgrade() -> None:
    """Insert vendor roles and application permissions."""
    role_table = sa.table(
        "role",
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("scope_type", sa.String()),
        sa.column("status", sa.String()),
        sa.column("is_system", sa.Boolean()),
        schema="iam",
    )
    permission_table = sa.table(
        "permission",
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String()),
        schema="iam",
    )
    mapping_table = sa.table(
        "role_permission",
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        schema="iam",
    )
    roles = [role for role in ROLES if role.code in NEW_ROLE_CODES]
    permissions = [
        permission
        for permission in PERMISSIONS
        if permission.code in NEW_PERMISSION_CODES
    ]
    _insert_idempotently(
        role_table,
        [
            {
                "role_id": role.role_id,
                "code": role.code,
                "name": role.name,
                "scope_type": role.scope_type.value,
                "status": "ACTIVE",
                "is_system": True,
            }
            for role in roles
        ],
        ["role_id"],
    )
    _insert_idempotently(
        permission_table,
        [
            {
                "permission_id": permission.permission_id,
                "code": permission.code,
                "description": permission.description,
                "status": "ACTIVE",
            }
            for permission in permissions
        ],
        ["permission_id"],
    )
    role_ids = {role.code: role.role_id for role in ROLES}
    permission_ids = {
        permission.code: permission.permission_id for permission in PERMISSIONS
    }
    mappings = [
        {
            "role_id": role_ids[role_code],
            "permission_id": permission_ids[permission_code],
        }
        for role_code, permission_codes in ROLE_PERMISSIONS.items()
        for permission_code in permission_codes
        if permission_code in NEW_PERMISSION_CODES
    ]
    _insert_idempotently(
        mapping_table,
        mappings,
        ["role_id", "permission_id"],
    )


def downgrade() -> None:
    """Remove only the vendor roles and application permissions."""
    permission_ids = ", ".join(
        f"'{permission.permission_id}'"
        for permission in PERMISSIONS
        if permission.code in NEW_PERMISSION_CODES
    )
    role_ids = ", ".join(
        f"'{role.role_id}'" for role in ROLES if role.code in NEW_ROLE_CODES
    )
    op.execute(
        sa.text(
            "DELETE FROM iam.role_permission "
            f"WHERE permission_id IN ({permission_ids})"
        )
    )
    op.execute(
        sa.text(f"DELETE FROM iam.permission WHERE permission_id IN ({permission_ids})")
    )
    op.execute(sa.text(f"DELETE FROM iam.role WHERE role_id IN ({role_ids})"))


def _insert_idempotently(
    table: sa.TableClause,
    rows: list[dict[str, object]],
    index_elements: list[str],
) -> None:
    """Insert catalog rows without failing when they already exist."""
    if not rows:
        return
    statement = postgresql.insert(table).values(rows)
    op.get_bind().execute(statement.on_conflict_do_nothing(index_elements=index_elements))
