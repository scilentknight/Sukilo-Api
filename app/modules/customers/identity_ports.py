"""Define typed ports for identity services used by customer workflows."""

from typing import Protocol
from uuid import UUID

from app.core.auth_context import AuthContext
from app.modules.customers.service import PreferenceView
from app.modules.identity.models import AuthGrant, UserAccount, UserSession
from app.modules.identity.types import (
    GrantPurpose,
    OtpChannel,
    OtpPurpose,
)


class ProfileAccountPort(Protocol):
    """Read the authenticated canonical account."""

    def get_by_id(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserAccount | None:
        """Return an account by stable ID."""
        ...


class ProfilePreferencePort(Protocol):
    """Read the authenticated account preference projection."""

    def get_preferences(self, actor: AuthContext) -> PreferenceView | None:
        """Return the account preference view when present."""
        ...


class ProfileSessionPort(Protocol):
    """List live sessions owned by one account."""

    def list_owned(self, user_id: UUID) -> list[UserSession]:
        """Return live sessions owned by the account."""
        ...


class IdentifierAccountPort(ProfileAccountPort, Protocol):
    """Lock accounts and check identifier uniqueness."""

    def get_by_email(
        self,
        email: str,
        *,
        for_update: bool = False,
    ) -> UserAccount | None:
        """Return an account by normalized email."""
        ...

    def get_by_phone(self, phone_e164: str) -> UserAccount | None:
        """Return an account by normalized phone."""
        ...

    def flush(self) -> None:
        """Flush staged account changes without committing."""
        ...


class CandidateOtpPort(Protocol):
    """Create owned verification challenges for candidate destinations."""

    def request(
        self,
        *,
        user_id: UUID | None,
        destination: str,
        channel: OtpChannel,
        purpose: OtpPurpose,
        locale_code: str,
    ) -> UUID:
        """Create and deliver one verification challenge."""
        ...


class ChangeGrantPort(Protocol):
    """Consume purpose-bound identifier-change grants."""

    def consume(
        self,
        raw_token: str,
        *,
        user_id: UUID | None,
        session_id: UUID | None,
        purpose: GrantPurpose,
        action_class: str | None,
        subject_hash: str | None,
    ) -> AuthGrant:
        """Validate bindings and consume one grant."""
        ...


class SessionRevocationPort(Protocol):
    """Revoke other sessions and advance account authentication state."""

    def revoke_all(
        self,
        user_id: UUID,
        *,
        current_session_id: UUID | None = None,
        keep_current: bool = False,
        reason: str = "USER_REVOKED_ALL",
        notify: bool = True,
    ) -> None:
        """Revoke owned sessions and increment `auth_version`."""
        ...


class DestinationDigestPort(Protocol):
    """Create keyed digests for normalized destinations."""

    def digest(self, purpose: str, *parts: str) -> str:
        """Create a self-describing digest."""
        ...
