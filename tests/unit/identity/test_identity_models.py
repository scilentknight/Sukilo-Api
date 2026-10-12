"""Test IAM SQLAlchemy model contracts."""

import importlib.util
from types import ModuleType


def _load_identity_models() -> ModuleType:
    """Load identity models after asserting that their package exists."""
    module_name = "app.modules.identity.models"
    assert importlib.util.find_spec(module_name) is not None
    return __import__(module_name, fromlist=["AuthIdentity", "UserAccount"])


def test_user_account_uses_iam_schema_and_expected_columns() -> None:
    """Register the canonical account under the IAM schema."""
    models = _load_identity_models()
    table = models.UserAccount.__table__
    assert table.fullname == "iam.user_account"
    assert {column.name for column in table.columns} == {
        "user_id",
        "email",
        "phone_e164",
        "password_hash",
        "email_verified_at",
        "phone_verified_at",
        "status",
        "auth_version",
        "last_login_at",
        "closed_at",
        "created_at",
        "updated_at",
        "version",
    }


def test_user_account_declares_identifier_indexes_and_status_check() -> None:
    """Enforce normalized identifier uniqueness and known account states."""
    models = _load_identity_models()
    table = models.UserAccount.__table__
    index_names = {index.name for index in table.indexes}
    check_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert {
        "uq_user_account_email_present",
        "uq_user_account_phone_present",
        "ix_user_account_status",
    }.issubset(index_names)
    assert "ck_user_account_status" in check_names


def test_identity_state_columns_convert_to_python_string_enums() -> None:
    """Round-trip constrained strings through Python enum types."""
    models = _load_identity_models()
    types = __import__(
        "app.modules.identity.types",
        fromlist=["AccountStatus", "AuthProvider"],
    )
    assert models.UserAccount.__table__.c.status.type.python_type is types.AccountStatus
    assert models.UserAccount.__table__.c.status.type.native_enum is False
    assert models.AuthIdentity.__table__.c.provider.type.python_type is types.AuthProvider
    assert models.AuthIdentity.__table__.c.provider.type.native_enum is False


def test_user_account_representation_excludes_password_hash() -> None:
    """Keep password hashes out of diagnostic representations."""
    models = _load_identity_models()
    account = models.UserAccount(
        email="buyer@example.com",
        password_hash="argon2-secret-hash",
    )
    representation = repr(account)
    assert "argon2-secret-hash" not in representation
    assert "password_hash" not in representation


def test_auth_identity_uses_separate_schema_qualified_table() -> None:
    """Register authentication identities separately from accounts."""
    models = _load_identity_models()
    table = models.AuthIdentity.__table__
    assert table.fullname == "iam.auth_identity"
    assert {column.name for column in table.columns} == {
        "auth_identity_id",
        "user_id",
        "provider",
        "provider_subject",
        "created_at",
    }
    assert "uq_auth_identity_provider_subject" in {
        constraint.name for constraint in table.constraints
    }
    foreign_key = next(iter(table.foreign_keys))
    assert foreign_key.target_fullname == "iam.user_account.user_id"
    assert foreign_key.ondelete == "RESTRICT"
