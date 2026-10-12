"""Test JWT key and digest-pepper rotation behavior and planning gates."""

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.security import DigestService
from app.modules.identity.tokens import TokenError, TokenService
from app.modules.identity.types import AccountStatus

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
TTL = timedelta(minutes=10)
SKEW = timedelta(seconds=30)
SECRET_MARKERS = ("PRIVATE KEY", "BEGIN ", "pepper-material")


class _Account:
    user_id = UUID(int=1)
    auth_version = 1
    status = AccountStatus.ACTIVE


def _pair() -> tuple[str, str]:
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


def _service(
    active: str,
    private_key: str,
    public_keys: dict[str, str],
    clock: datetime = NOW,
) -> TokenService:
    return TokenService(
        issuer="https://auth.sukilo.test",
        audience="sukilo-api",
        active_key_id=active,
        private_key=private_key,
        public_keys=public_keys,
        access_ttl=TTL,
        clock=lambda: clock,
        clock_skew=SKEW,
    )


def _issue(service: TokenService) -> str:
    return service.issue_access_token(
        user_id=UUID(int=1),
        session_id=UUID(int=2),
        auth_version=1,
        authentication_methods=("password",),
        authenticated_at=NOW,
    )


def _validate(service: TokenService, token: str) -> None:
    service.validate_access_token(token, account=_Account(), correlation_id=UUID(int=3))


def test_mixed_key_validation_across_the_rotation_window() -> None:
    """Accept old and new tokens while both verification keys are published."""
    old_private, old_public = _pair()
    new_private, new_public = _pair()
    both = {"old": old_public, "new": new_public}
    old_token = _issue(_service("old", old_private, {"old": old_public}))
    new_token = _issue(_service("new", new_private, both))
    verifier = _service("new", new_private, both)
    _validate(verifier, old_token)
    _validate(verifier, new_token)


def test_signing_before_verifiers_publish_the_key_fails_closed() -> None:
    """Show why the verification key must be published before activation."""
    old_private, old_public = _pair()
    new_private, new_public = _pair()
    early_token = _issue(_service("new", new_private, {"new": new_public}))
    stale_verifier = _service("old", old_private, {"old": old_public})
    with pytest.raises(TokenError):
        _validate(stale_verifier, early_token)


def test_removed_key_rejects_old_tokens_only_after_removal() -> None:
    """Reject tokens from a removed key and keep validating the new key."""
    old_private, old_public = _pair()
    new_private, new_public = _pair()
    old_token = _issue(_service("old", old_private, {"old": old_public}))
    after_removal = _service("new", new_private, {"new": new_public})
    with pytest.raises(TokenError):
        _validate(after_removal, old_token)
    _validate(after_removal, _issue(after_removal))


def test_wrong_key_for_a_known_kid_and_unknown_kid_fail() -> None:
    """Reject a token whose kid names a different public key or none."""
    first_private, first_public = _pair()
    _, second_public = _pair()
    token = _issue(_service("k1", first_private, {"k1": first_public}))
    swapped = _service("k1", first_private, {"k1": second_public})
    with pytest.raises(TokenError):
        _validate(swapped, token)
    unknown = _service("k2", first_private, {"k2": second_public})
    with pytest.raises(TokenError):
        _validate(unknown, token)


def test_old_token_expires_after_ttl_plus_skew_even_if_key_stays() -> None:
    """Treat token lifetime as the bound for safe old-key removal."""
    private, public = _pair()
    token = _issue(_service("k1", private, {"k1": public}))
    inside = _service("k1", private, {"k1": public}, NOW + TTL + SKEW - timedelta(seconds=1))
    outside = _service("k1", private, {"k1": public}, NOW + TTL + SKEW + timedelta(seconds=1))
    _validate(inside, token)
    with pytest.raises(TokenError):
        _validate(outside, token)


def test_pepper_versions_stay_readable_until_retired() -> None:
    """Verify old digests while configured, and not after retirement."""
    both = {"v1": b"a" * 32, "v2": b"b" * 32}
    old_digest = DigestService(active_key_id="v1", peppers=both).digest("refresh", "value")
    rotated = DigestService(active_key_id="v2", peppers=both)
    assert rotated.verify(old_digest, "refresh", "value")
    assert rotated.needs_rotation(old_digest)
    assert not rotated.needs_rotation(rotated.digest("refresh", "value"))
    retired = DigestService(active_key_id="v2", peppers={"v2": both["v2"]})
    assert not retired.verify(old_digest, "refresh", "value")


def _load_script() -> ModuleType:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location(
            "rotate_iam_keys", scripts / "rotate_iam_keys.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(scripts))


@pytest.fixture(scope="module")
def script() -> ModuleType:
    """Load the rotation planning script."""
    return _load_script()


def test_script_requires_verification_key_before_activation(script: ModuleType) -> None:
    """Refuse activating a key that was never published."""
    refused = script.plan_activate(["old"], "old", "new", NOW)
    assert not refused.allowed and "before activating" in refused.refusal
    staged = script.plan_add_verification_key(["old"], "new", NOW)
    assert staged.allowed
    allowed = script.plan_activate(["old", "new"], "old", "new", NOW)
    assert allowed.allowed
    assert not script.plan_add_verification_key(["old"], "old", NOW).allowed
    assert not script.plan_add_verification_key([], "bad id!", NOW).allowed


def test_script_refuses_removal_inside_ttl_plus_skew(script: ModuleType) -> None:
    """Allow removal only after cutover plus access TTL plus skew."""
    cutover = NOW
    earliest = cutover + TTL + SKEW
    args = (["old", "new"], "new", "old", cutover)
    inside = script.plan_remove(*args, earliest - timedelta(seconds=1), TTL, SKEW)
    assert not inside.allowed and inside.earliest_at == earliest.isoformat()
    outside = script.plan_remove(*args, earliest, TTL, SKEW)
    assert outside.allowed
    active = script.plan_remove(["old", "new"], "old", "old", cutover, earliest, TTL, SKEW)
    assert not active.allowed
    unknown = script.plan_remove(["new"], "new", "ghost", cutover, earliest, TTL, SKEW)
    assert not unknown.allowed


def test_script_reports_readable_pepper_versions(script: ModuleType) -> None:
    """List readable versions with the active version first."""
    plan = script.plan_pepper_status("v2", ["v1", "v2"], NOW)
    assert plan.allowed and plan.readable_pepper_versions == ("v2", "v1")
    assert not script.plan_pepper_status("v3", ["v1", "v2"], NOW).allowed


def test_script_gates_pepper_retirement_on_dependent_expiry(script: ModuleType) -> None:
    """Refuse retirement until dependent values have expired."""
    ttl = timedelta(days=30)
    since = NOW
    early = script.plan_pepper_retire("v2", ["v1", "v2"], "v1", since, NOW + timedelta(days=1), ttl)
    assert not early.allowed and early.earliest_at == (since + ttl).isoformat()
    late = script.plan_pepper_retire("v2", ["v1", "v2"], "v1", since, since + ttl, ttl)
    assert late.allowed and late.readable_pepper_versions == ("v2",)
    assert not script.plan_pepper_retire("v2", ["v1", "v2"], "v2", since, since + ttl, ttl).allowed


def test_script_output_never_contains_key_or_pepper_material(
    script: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print only IDs, times, and steps."""
    code = script.main(
        ["pepper-status", "--active-pepper", "v2", "--configured-peppers", "v1,v2"]
    )
    out = capsys.readouterr().out
    assert code == 0 and json.loads(out)["readable_pepper_versions"] == ["v2", "v1"]
    code = script.main(
        ["jwt-activate", "--kid", "new", "--public-kids", "old", "--active-kid", "old"]
    )
    assert code == 2
    out += capsys.readouterr().out
    assert not any(marker in out for marker in SECRET_MARKERS)
    source = Path(script.__file__).read_text(encoding="utf-8")
    assert "PRIVATE_KEY_REFERENCE" not in source and "PEPPER_REFERENCE" not in source


def test_script_rejects_bad_timestamps_with_usage_exit(script: ModuleType) -> None:
    """Exit with the usage code on invalid input."""
    code = script.main(
        ["jwt-remove", "--kid", "old", "--public-kids", "old,new", "--active-kid", "new",
         "--cutover-at", "not-a-time"]
    )
    assert code == 3
