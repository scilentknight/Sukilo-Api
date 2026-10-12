"""Project the authenticated account into a privacy-safe customer profile."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.auth_context import AuthContext
from app.modules.customers.identity_ports import (
    ProfileAccountPort,
    ProfilePreferencePort,
    ProfileSessionPort,
)
from app.modules.customers.service import CustomerServiceError, PreferenceView
from app.modules.identity.types import AccountStatus


class ProfileUnavailableError(CustomerServiceError):
    """Report that no safe profile can be produced for the principal."""


@dataclass(frozen=True, slots=True)
class CurrentSessionView:
    """Expose the active session and device indicator only."""

    session_id: UUID
    device_id: str | None
    device_name: str | None
    issued_at: datetime
    is_current: bool


@dataclass(frozen=True, slots=True)
class ProfileView:
    """Expose the safe `/me` account projection."""

    user_id: UUID
    status: AccountStatus
    email_masked: str | None
    phone_masked: str | None
    email_verified: bool
    phone_verified: bool
    created_at: datetime
    last_login_at: datetime | None
    preferences: PreferenceView | None
    current_session: CurrentSessionView


class ProfileService:
    """Compose account, preference, and session facts without side effects."""

    def __init__(
        self,
        *,
        accounts: ProfileAccountPort,
        preferences: ProfilePreferencePort,
        sessions: ProfileSessionPort,
    ) -> None:
        """Initialize read-only profile collaborators."""
        self.accounts = accounts
        self.preferences = preferences
        self.sessions = sessions

    def get_profile(self, actor: AuthContext) -> ProfileView:
        """Return the authenticated customer's masked profile.

        Args:
            actor: Authenticated principal; the only source of the user ID.

        Returns:
            Masked account projection with preferences and current session.

        Raises:
            ProfileUnavailableError: If the account is missing or inactive, or
                the current session is no longer live.
        """
        account = self.accounts.get_by_id(actor.user_id)
        if account is None or account.status != AccountStatus.ACTIVE:
            raise ProfileUnavailableError("Profile is unavailable.")
        current = self._current_session(actor)
        return ProfileView(
            user_id=account.user_id,
            status=account.status,
            email_masked=mask_email(account.email) if account.email else None,
            phone_masked=mask_phone(account.phone_e164) if account.phone_e164 else None,
            email_verified=account.email_verified_at is not None,
            phone_verified=account.phone_verified_at is not None,
            created_at=account.created_at,
            last_login_at=account.last_login_at,
            preferences=self.preferences.get_preferences(actor),
            current_session=current,
        )

    def _current_session(self, actor: AuthContext) -> CurrentSessionView:
        for row in self.sessions.list_owned(actor.user_id):
            if row.session_id == actor.session_id and row.user_id == actor.user_id:
                return CurrentSessionView(
                    session_id=row.session_id,
                    device_id=row.device_id,
                    device_name=row.device_name,
                    issued_at=row.issued_at,
                    is_current=True,
                )
        raise ProfileUnavailableError("Profile is unavailable.")


def mask_email(value: str) -> str:
    """Mask an email address while keeping its domain.

    Args:
        value: Normalized email address.

    Returns:
        The first local-part character, a fixed mask, and the domain.
    """
    local, _, domain = value.partition("@")
    visible = local[:1] if len(local) > 1 else ""
    mask = "***" if visible else "*"
    return f"{visible}{mask}@{domain}"


def mask_phone(value: str) -> str:
    """Mask an E.164 phone number except its final three digits.

    Args:
        value: Phone number in E.164 format.

    Returns:
        A plus sign, masked leading digits, and the last three digits.
    """
    digits = value.removeprefix("+")
    return f"+{'*' * max(len(digits) - 3, 1)}{digits[-3:]}"
