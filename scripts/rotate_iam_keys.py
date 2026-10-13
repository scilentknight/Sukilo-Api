"""Plan and gate JWT-key and digest-pepper rotation without touching secrets.

This is a safe planning command, not a secret-store client. It reads only key
IDs, versions, TTLs, and timestamps. It never loads, prints, or generates real
private keys or peppers, and it makes no network calls.

Commands:
    jwt-add         Stage a new verification key before it signs anything.
    jwt-activate    Allow signing cutover only to an already-published key.
    jwt-remove      Allow removal only after access-token TTL plus skew.
    pepper-status   Report which pepper versions remain readable.
    pepper-retire   Allow retiring a pepper only after dependents expire.
"""

import argparse
import json
import re
import secrets
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import _bootstrap  # noqa: F401

from app.core.security import DigestConfigurationError, DigestService

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_USAGE = 3
DEFAULT_SKEW_SECONDS = 30
DEFAULT_ACCESS_TTL_SECONDS = 600
DEFAULT_DEPENDENT_TTL_SECONDS = 2_592_000
_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True, slots=True)
class RotationPlan:
    """Describe a rotation step, its gate result, and the audit record."""

    command: str
    allowed: bool
    steps: tuple[str, ...] = ()
    refusal: str | None = None
    earliest_at: str | None = None
    readable_pepper_versions: tuple[str, ...] = ()
    audit: dict[str, str] = field(default_factory=dict)


def _audit(command: str, subject: str, allowed: bool, now: datetime) -> dict[str, str]:
    """Build a secret-free audit record for one planned stage."""
    return {
        "stage": command,
        "subject": subject,
        "result": "PLANNED" if allowed else "REFUSED",
        "at": now.isoformat(),
    }


def _refuse(command: str, subject: str, reason: str, now: datetime, **extra: str | None) -> RotationPlan:
    """Return a refusal plan."""
    return RotationPlan(
        command=command,
        allowed=False,
        refusal=reason,
        earliest_at=extra.get("earliest_at"),
        audit=_audit(command, subject, False, now),
    )


def valid_key_id(value: str) -> bool:
    """Return whether a key ID or pepper version is a safe identifier."""
    return _KEY_ID.fullmatch(value) is not None


def plan_add_verification_key(
    public_kids: Sequence[str],
    new_kid: str,
    now: datetime,
) -> RotationPlan:
    """Plan publishing a new verification key before activation.

    Args:
        public_kids: Currently published verification key IDs.
        new_kid: ID of the new key.
        now: Current time for the audit record.

    Returns:
        A plan that is refused when the ID is invalid or already published.
    """
    command = "jwt-add"
    if not valid_key_id(new_kid):
        return _refuse(command, "invalid", "Key ID is not a safe identifier.", now)
    if new_kid in public_kids:
        return _refuse(command, new_kid, "Key ID is already published.", now)
    steps = (
        f"Generate the signing key for kid '{new_kid}' inside the secret manager.",
        "Publish only its PUBLIC key into JWT_PUBLIC_KEY_SET beside existing keys.",
        "Deploy to every verifier and wait for full rollout; do not change the active kid.",
        "Record this stage in the change ticket and audit trail.",
    )
    return RotationPlan(command, True, steps, audit=_audit(command, new_kid, True, now))


def plan_activate(
    public_kids: Sequence[str],
    active_kid: str | None,
    new_kid: str,
    now: datetime,
) -> RotationPlan:
    """Plan the signing cutover to an already-published verification key.

    Args:
        public_kids: Currently published verification key IDs.
        active_kid: Current signing key ID.
        new_kid: Key ID to activate for signing.
        now: Current time for the audit record.

    Returns:
        A plan that is refused unless the key was published first.
    """
    command = "jwt-activate"
    if new_kid not in public_kids:
        return _refuse(command, new_kid, "Add the verification key before activating it.", now)
    if new_kid == active_kid:
        return _refuse(command, new_kid, "Key is already the active signing key.", now)
    steps = (
        f"Set JWT_ACTIVE_KEY_ID to '{new_kid}' and point the private-key reference at it.",
        f"Keep '{active_kid}' in JWT_PUBLIC_KEY_SET for verification only.",
        "Revoke signing access to the old private key immediately after cutover.",
        "Record the cutover time; jwt-remove needs it for the earliest removal time.",
    )
    return RotationPlan(command, True, steps, audit=_audit(command, new_kid, True, now))


def plan_remove(
    public_kids: Sequence[str],
    active_kid: str | None,
    old_kid: str,
    cutover_at: datetime,
    now: datetime,
    access_ttl: timedelta,
    skew: timedelta,
) -> RotationPlan:
    """Plan removing an old verification key after the token window.

    Args:
        public_kids: Currently published verification key IDs.
        active_kid: Current signing key ID.
        old_kid: Key ID to remove.
        cutover_at: When signing moved off the old key.
        now: Current time.
        access_ttl: Access-token lifetime.
        skew: Maximum accepted clock skew.

    Returns:
        A plan that is refused while tokens signed by the key may be valid.
    """
    command = "jwt-remove"
    earliest = cutover_at + access_ttl + skew
    if old_kid not in public_kids:
        return _refuse(command, old_kid, "Key ID is not published.", now)
    if old_kid == active_kid:
        return _refuse(command, old_kid, "Cannot remove the active signing key.", now)
    if now < earliest:
        return _refuse(
            command, old_kid,
            "Key is still inside access-token TTL plus clock skew.",
            now, earliest_at=earliest.isoformat(),
        )
    steps = (
        f"Remove '{old_kid}' from JWT_PUBLIC_KEY_SET and deploy to every verifier.",
        "Confirm tokens with the removed kid now fail validation.",
        "Record this stage in the change ticket and audit trail.",
    )
    return RotationPlan(
        command, True, steps, earliest_at=earliest.isoformat(),
        audit=_audit(command, old_kid, True, now),
    )


def readable_pepper_versions(active: str, configured: Sequence[str]) -> tuple[str, ...]:
    """Return pepper versions DigestService will still verify.

    Ephemeral random material stands in for real peppers; only the version
    set and DigestService semantics matter.

    Args:
        active: Version used for new digests.
        configured: Versions loaded for verification.

    Returns:
        Readable versions, active first.

    Raises:
        DigestConfigurationError: If the active version is not configured.
    """
    material = {version: secrets.token_bytes(32) for version in configured}
    service = DigestService(active_key_id=active, peppers=material)
    readable = []
    for version in configured:
        probe = DigestService(active_key_id=version, peppers=material).digest("probe", "x")
        if service.verify(probe, "probe", "x"):
            readable.append(version)
    return tuple(sorted(readable, key=lambda item: (item != active, item)))


def plan_pepper_status(active: str, configured: Sequence[str], now: datetime) -> RotationPlan:
    """Report which pepper versions remain readable.

    Args:
        active: Active pepper version.
        configured: Configured pepper versions.
        now: Current time for the audit record.

    Returns:
        A plan listing readable versions, or a refusal for bad config.
    """
    command = "pepper-status"
    if not all(valid_key_id(item) for item in (active, *configured)):
        return _refuse(command, "invalid", "Pepper version is not a safe identifier.", now)
    try:
        readable = readable_pepper_versions(active, configured)
    except DigestConfigurationError:
        return _refuse(command, active, "Active pepper version is not configured.", now)
    return RotationPlan(
        command, True, readable_pepper_versions=readable,
        steps=("New writes use the active version; older readable versions rotate on use.",),
        audit=_audit(command, active, True, now),
    )


def plan_pepper_retire(
    active: str,
    configured: Sequence[str],
    retire: str,
    active_since: datetime,
    now: datetime,
    dependent_ttl: timedelta,
) -> RotationPlan:
    """Plan retiring an old pepper once dependent values have expired.

    Args:
        active: Active pepper version.
        configured: Configured pepper versions.
        retire: Version to retire.
        active_since: When the active version began taking new writes.
        now: Current time.
        dependent_ttl: Longest lifetime of any digest-backed value.

    Returns:
        A plan that is refused while digests under the version may be live.
    """
    command = "pepper-retire"
    earliest = active_since + dependent_ttl
    if retire == active or retire not in configured:
        return _refuse(command, retire, "Version is active or not configured.", now)
    if now < earliest:
        return _refuse(
            command, retire, "Dependent values may still use this version.",
            now, earliest_at=earliest.isoformat(),
        )
    remaining = tuple(item for item in configured if item != retire)
    status = plan_pepper_status(active, remaining, now)
    return RotationPlan(
        command, status.allowed,
        steps=(f"Remove version '{retire}' from the configured pepper set and redeploy.",
               "Suspected compromise instead: follow the incident runbook to revoke sessions."),
        refusal=status.refusal, earliest_at=earliest.isoformat(),
        readable_pepper_versions=status.readable_pepper_versions,
        audit=_audit(command, retire, status.allowed, now),
    )


def _defaults() -> tuple[list[str], str | None, int]:
    """Read non-secret defaults (key IDs and TTL) from application settings."""
    try:
        from app.core.config import settings
        return (
            sorted(settings.JWT_PUBLIC_KEY_SET),
            settings.JWT_ACTIVE_KEY_ID,
            settings.ACCESS_TOKEN_TTL_SECONDS,
        )
    except ValueError:
        return [], None, DEFAULT_ACCESS_TTL_SECONDS


def _csv(value: str | None) -> list[str]:
    """Split a comma-separated option into non-empty items."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _time(value: str | None, fallback: datetime) -> datetime:
    """Parse an ISO timestamp as UTC, or return the fallback."""
    if value is None:
        return fallback
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Plan IAM key rotation.")
    parser.add_argument("command", choices=[
        "jwt-add", "jwt-activate", "jwt-remove", "pepper-status", "pepper-retire",
    ])
    parser.add_argument("--kid", help="JWT key ID.")
    parser.add_argument("--public-kids", help="Comma-separated published key IDs.")
    parser.add_argument("--active-kid", help="Current signing key ID.")
    parser.add_argument("--cutover-at", help="ISO time signing left the old key.")
    parser.add_argument("--access-ttl-seconds", type=int)
    parser.add_argument("--skew-seconds", type=int, default=DEFAULT_SKEW_SECONDS)
    parser.add_argument("--active-pepper", help="Active pepper version.")
    parser.add_argument("--configured-peppers", help="Comma-separated versions.")
    parser.add_argument("--retire", help="Pepper version to retire.")
    parser.add_argument("--active-since", help="ISO time active pepper began.")
    parser.add_argument("--dependent-ttl-seconds", type=int, default=DEFAULT_DEPENDENT_TTL_SECONDS)
    parser.add_argument("--now", help="ISO time override for rehearsals.")
    return parser


def _dispatch(args: argparse.Namespace, now: datetime) -> RotationPlan:
    """Select and run the requested planning function."""
    default_kids, default_active, default_ttl = _defaults()
    kids = _csv(args.public_kids) or default_kids
    active_kid = args.active_kid or default_active
    ttl = timedelta(seconds=args.access_ttl_seconds or default_ttl)
    peppers = _csv(args.configured_peppers)
    if args.command == "jwt-add":
        return plan_add_verification_key(kids, args.kid or "", now)
    if args.command == "jwt-activate":
        return plan_activate(kids, active_kid, args.kid or "", now)
    if args.command == "jwt-remove":
        cutover = _time(args.cutover_at, now)
        return plan_remove(kids, active_kid, args.kid or "", cutover, now, ttl,
                           timedelta(seconds=args.skew_seconds))
    if args.command == "pepper-status":
        return plan_pepper_status(args.active_pepper or "", peppers, now)
    since = _time(args.active_since, now)
    return plan_pepper_retire(args.active_pepper or "", peppers, args.retire or "", since, now,
                              timedelta(seconds=args.dependent_ttl_seconds))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the planning command and print a secret-free JSON plan.

    Args:
        argv: Optional argument list; defaults to ``sys.argv``.

    Returns:
        0 when the step is allowed, 2 when a gate refuses it, 3 on bad input.
    """
    args = _build_parser().parse_args(argv)
    try:
        plan = _dispatch(args, _time(args.now, datetime.now(UTC)))
    except ValueError as exc:
        print(f"Invalid input: {type(exc).__name__}", file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps(asdict(plan), sort_keys=True, indent=2))
    return EXIT_OK if plan.allowed else EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
