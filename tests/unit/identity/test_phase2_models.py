"""Test Phase 2 IAM model metadata."""

from app.modules.identity import models


def test_otp_challenge_matches_security_contract() -> None:
    """Require OTP security fields, constraints, and indexes."""
    table = models.OtpChallenge.__table__
    assert table.fullname == "iam.otp_challenge"
    assert {column.name for column in table.columns} == {
        "otp_id",
        "user_id",
        "destination_hash",
        "otp_nonce",
        "channel",
        "purpose",
        "otp_hash",
        "attempt_count",
        "max_attempts",
        "expires_at",
        "verified_at",
        "invalidated_at",
        "created_at",
    }
    assert {
        "ix_otp_challenge_destination_purpose_created",
        "ix_otp_challenge_user_created",
        "ix_otp_challenge_expires_unverified",
    }.issubset({index.name for index in table.indexes})


def test_user_session_matches_rotation_contract() -> None:
    """Require refresh rotation fields and indexes."""
    table = models.UserSession.__table__
    assert table.fullname == "iam.user_session"
    assert {
        "session_id",
        "family_id",
        "user_id",
        "refresh_token_hash",
        "expires_at",
        "rotated_at",
        "replaced_by_session_id",
        "revoked_at",
    }.issubset({column.name for column in table.columns})
    assert {
        "uq_user_session_refresh_token_hash",
        "ix_user_session_user_active",
        "ix_user_session_family",
        "ix_user_session_expires_active",
    }.issubset({index.name for index in table.indexes})
    assert all(
        foreign_key.ondelete == "RESTRICT"
        for foreign_key in table.foreign_keys
    )


def test_auth_grant_matches_single_use_contract() -> None:
    """Require authorization grant binding and lifecycle fields."""
    table = models.AuthGrant.__table__
    assert table.fullname == "iam.auth_grant"
    assert {
        "grant_id",
        "user_id",
        "session_id",
        "purpose",
        "action_class",
        "subject_hash",
        "token_hash",
        "authentication_methods",
        "issued_at",
        "expires_at",
        "consumed_at",
        "revoked_at",
        "correlation_id",
    } == {column.name for column in table.columns}
    assert {
        "uq_auth_grant_token_hash",
        "ix_auth_grant_user_purpose_active",
        "ix_auth_grant_session_active",
        "ix_auth_grant_expires_active",
    }.issubset({index.name for index in table.indexes})
