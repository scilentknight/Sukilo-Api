"""Seed the IAM access-control catalog.

Revision ID: 20261012_0013
Revises: 20261012_0012
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0013"
down_revision: str | Sequence[str] | None = "20261012_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = (
    ("725f1e7e-00e9-5881-872f-06cc64dbf6fa", "SUPER_ADMIN", "Super Administrator", "GLOBAL"),
    ("9462dd74-c145-56d2-bd08-ad90f0bf0d99", "COUNTRY_ADMIN", "Country Administrator", "COUNTRY"),
    ("a861b1f3-be6e-593f-b1d3-933d524f2fc8", "COUNTRY_FINANCE", "Country Finance", "COUNTRY"),
    ("7752f3b8-9770-59c1-8d21-b3dc99d2604d", "COUNTRY_OPERATIONS", "Country Operations", "COUNTRY"),
    ("86435e4c-d8b7-543b-8dc3-958ab833252b", "MODERATOR_SUPPORT", "Moderator Support", "COUNTRY"),
)

PERMISSIONS = (
    ("e98f7748-b201-5c32-b7a2-3b432f4016e8", "iam.role.read", "Read role definitions."),
    ("6535585a-ea39-54f0-8a46-6a5200bd8967", "iam.permission.read", "Read permission definitions."),
    ("92a9607c-58a6-5c28-acd5-83e362bc2904", "iam.assignment.read", "Read scoped role assignments."),
    ("5a9f9976-767a-5b62-8ac4-5783b5878132", "iam.assignment.grant", "Grant scoped role assignments."),
    ("ef6ebec2-b944-562c-b001-c75cf239c283", "iam.assignment.revoke", "Revoke scoped role assignments."),
    ("4a4ad20d-f5dc-5eca-94c2-5e9263b6de8b", "iam.session.read", "Read account session metadata."),
    ("39deb9f4-d2db-5014-8c57-e662bd70f526", "iam.session.revoke", "Revoke account sessions."),
    ("94f42236-31b3-51e3-8061-be7b1dc18ba8", "customer.profile.read", "Read customer profiles."),
    ("8017ce94-56c3-52a5-9f95-1cce4e4ff6d3", "customer.profile.write", "Update customer profiles."),
    ("5734be3a-a4cb-5a66-9ee3-11f71fe1261e", "customer.preference.write", "Update customer preferences."),
    ("f4af06b4-d6af-5171-8b23-1eea0d355cfc", "customer.consent.write", "Record customer consent."),
    ("27af959f-067d-5f7a-8367-706193723093", "customer.address.read", "Read customer addresses."),
    ("6729a537-a1a9-5387-954d-a2574910cc1c", "customer.address.write", "Manage customer addresses."),
    ("6fff3e7b-0f76-5f04-a103-f1b161d62705", "country.settings.read", "Read country settings."),
    ("544620b9-59d7-59b6-8303-65910157486e", "country.settings.write", "Update country settings."),
    ("271dea72-a92a-5d11-a9ec-aefcc7551f41", "vendor.approve", "Approve vendors."),
    ("5af3020a-4f27-5528-8b13-0ef8871c37da", "catalog.product.write", "Manage catalog products."),
    ("a7232b3a-2f25-5dd3-93b6-516b9455faca", "order.cancel", "Cancel orders."),
    ("2dcccc61-0f93-5609-9209-45352290d0c3", "refund.approve", "Approve refunds."),
    ("523cce80-4a24-5c6a-ac6e-82b6dad35b51", "finance.payout.release", "Release finance payouts."),
)

COUNTRY_ADMIN_CODES = {
    "iam.role.read",
    "iam.permission.read",
    "iam.assignment.read",
    "iam.assignment.grant",
    "iam.assignment.revoke",
    "iam.session.read",
    "iam.session.revoke",
}

ROLE_PERMISSIONS = {
    "SUPER_ADMIN": frozenset(code for _id, code, _description in PERMISSIONS),
    "COUNTRY_ADMIN": frozenset(COUNTRY_ADMIN_CODES),
    "COUNTRY_FINANCE": frozenset(),
    "COUNTRY_OPERATIONS": frozenset(),
    "MODERATOR_SUPPORT": frozenset(),
}


def upgrade() -> None:
    """Insert deterministic system roles and permissions."""
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
    _insert_idempotently(
        role_table,
        [
            {
                "role_id": role_id,
                "code": code,
                "name": name,
                "scope_type": scope_type,
                "status": "ACTIVE",
                "is_system": True,
            }
            for role_id, code, name, scope_type in ROLES
        ],
        ["role_id"],
    )
    _insert_idempotently(
        permission_table,
        [
            {
                "permission_id": permission_id,
                "code": code,
                "description": description,
                "status": "ACTIVE",
            }
            for permission_id, code, description in PERMISSIONS
        ],
        ["permission_id"],
    )
    role_ids = {code: role_id for role_id, code, _name, _scope in ROLES}
    permission_ids = {
        code: permission_id
        for permission_id, code, _description in PERMISSIONS
    }
    mappings = [
        {
            "role_id": role_ids[role_code],
            "permission_id": permission_ids[permission_code],
        }
        for role_code, permission_codes in ROLE_PERMISSIONS.items()
        for permission_code in permission_codes
    ]
    _insert_idempotently(
        mapping_table,
        mappings,
        ["role_id", "permission_id"],
    )


def downgrade() -> None:
    """Delete only the deterministic access catalog rows."""
    role_ids = ", ".join(f"'{role_id}'" for role_id, *_rest in ROLES)
    permission_ids = ", ".join(
        f"'{permission_id}'"
        for permission_id, _code, _description in PERMISSIONS
    )
    op.execute(
        sa.text(
            f"DELETE FROM iam.role_permission WHERE role_id IN ({role_ids})"
        )
    )
    op.execute(
        sa.text(
            f"DELETE FROM iam.permission WHERE permission_id IN "
            f"({permission_ids})"
        )
    )
    op.execute(sa.text(f"DELETE FROM iam.role WHERE role_id IN ({role_ids})"))


def _insert_idempotently(
    table: sa.TableClause,
    rows: list[dict[str, object]],
    index_elements: list[str],
) -> None:
    statement = postgresql.insert(table).values(rows)
    op.get_bind().execute(
        statement.on_conflict_do_nothing(index_elements=index_elements)
    )
