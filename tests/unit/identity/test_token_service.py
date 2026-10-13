"""Test asymmetric access-token contracts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.modules.identity.tokens import TokenError, TokenService
from app.modules.identity.types import AccountStatus


class AccountStub:
    """Provide account state required during token validation."""

    user_id = UUID(int=1)
    auth_version = 3
    status = AccountStatus.ACTIVE


def _keys() -> tuple[str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def test_access_token_validates_required_identity_claims() -> None:
    """Issue a token and return a tenant-neutral auth context."""
    private_key, public_key = _keys()
    now = datetime(2026, 10, 12, tzinfo=UTC)
    service = TokenService(
        issuer="https://auth.sukilo.test",
        audience="sukilo-api",
        active_key_id="key-1",
        private_key=private_key,
        public_keys={"key-1": public_key},
        access_ttl=timedelta(minutes=10),
        clock=lambda: now,
    )
    token = service.issue_access_token(
        user_id=UUID(int=1),
        session_id=UUID(int=2),
        auth_version=3,
        authentication_methods=("password",),
        authenticated_at=now,
    )
    context = service.validate_access_token(
        token,
        account=AccountStub(),
        correlation_id=UUID(int=4),
    )
    assert context.user_id == UUID(int=1)
    assert context.session_id == UUID(int=2)
    assert not hasattr(context, "country_id")


def test_unknown_signing_key_and_auth_version_fail_closed() -> None:
    """Reject unknown keys and invalidated account versions."""
    private_key, public_key = _keys()
    now = datetime(2026, 10, 12, tzinfo=UTC)
    issuer = TokenService(
        issuer="issuer",
        audience="audience",
        active_key_id="old",
        private_key=private_key,
        public_keys={"old": public_key},
        access_ttl=timedelta(minutes=10),
        clock=lambda: now,
    )
    token = issuer.issue_access_token(
        user_id=UUID(int=1),
        session_id=UUID(int=2),
        auth_version=2,
        authentication_methods=("password",),
        authenticated_at=now,
    )
    validator = TokenService(
        issuer="issuer",
        audience="audience",
        active_key_id="new",
        private_key=private_key,
        public_keys={"new": public_key},
        access_ttl=timedelta(minutes=10),
        clock=lambda: now,
    )
    with pytest.raises(TokenError):
        validator.validate_access_token(
            token,
            account=AccountStub(),
            correlation_id=UUID(int=4),
        )
