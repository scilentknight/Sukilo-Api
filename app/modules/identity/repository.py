"""Persist identity and authentication state with parameterized statements."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.modules.identity.models import (
    AuthGrant,
    AuthIdentity,
    OtpChallenge,
    UserAccount,
    UserSession,
)
from app.modules.identity.types import GrantPurpose, OtpPurpose


class AccountRepository:
    """Provide focused persistence operations for user accounts."""

    def __init__(self, db: Session) -> None:
        """Initialize the repository.

        Args:
            db: Active SQLAlchemy session.
        """
        self.db = db

    def get_by_id(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserAccount | None:
        """Return an account by its stable identifier.

        Args:
            user_id: Stable account identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching account, or `None`.
        """
        statement = select(UserAccount).where(UserAccount.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_by_email(
        self,
        email: str,
        *,
        for_update: bool = False,
    ) -> UserAccount | None:
        """Return an account by normalized email.

        Args:
            email: Normalized email address.

        Returns:
            Matching account, or `None`.
        """
        statement = select(UserAccount).where(UserAccount.email == email)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_by_phone(self, phone_e164: str) -> UserAccount | None:
        """Return an account by normalized phone number.

        Args:
            phone_e164: Phone number in E.164 format.

        Returns:
            Matching account, or `None`.
        """
        statement = select(UserAccount).where(
            UserAccount.phone_e164 == phone_e164,
        )
        return self.db.scalar(statement)

    def add(self, account: UserAccount) -> None:
        """Stage an account in the active transaction.

        Args:
            account: Account to stage.
        """
        self.db.add(account)

    def add_identity(self, identity: AuthIdentity) -> None:
        """Stage an authentication identity in the active transaction.

        Args:
            identity: Authentication identity to stage.
        """
        self.db.add(identity)

    def flush(self) -> None:
        """Flush staged identity changes without committing."""
        self.db.flush()

    def identifier_exists(
        self,
        email: str | None,
        phone_e164: str | None,
    ) -> bool:
        """Return whether any supplied identifier is already registered.

        Args:
            email: Optional normalized email.
            phone_e164: Optional normalized phone.

        Returns:
            `True` when a matching account exists.
        """
        clauses = []
        if email is not None:
            clauses.append(UserAccount.email == email)
        if phone_e164 is not None:
            clauses.append(UserAccount.phone_e164 == phone_e164)
        if not clauses:
            return False
        statement = select(UserAccount.user_id).where(or_(*clauses)).limit(1)
        return self.db.scalar(statement) is not None

    def delete_identities_by_user(self, user_id: UUID) -> None:
        """Delete login identities owned by one account."""
        self.db.execute(
            delete(AuthIdentity).where(AuthIdentity.user_id == user_id)
        )


class OtpRepository:
    """Persist and lock OTP challenges."""

    def __init__(self, db: Session) -> None:
        """Initialize the repository."""
        self.db = db

    def invalidate_active(
        self,
        destination_hash: str,
        purpose: OtpPurpose,
        now: datetime,
    ) -> None:
        """Invalidate matching unconsumed challenges."""
        statement = (
            update(OtpChallenge)
            .where(
                OtpChallenge.destination_hash == destination_hash,
                OtpChallenge.purpose == purpose,
                OtpChallenge.verified_at.is_(None),
                OtpChallenge.invalidated_at.is_(None),
            )
            .values(invalidated_at=now)
        )
        self.db.execute(statement)

    def add(self, challenge: OtpChallenge) -> None:
        """Stage an OTP challenge."""
        self.db.add(challenge)

    def get_for_update(self, otp_id: UUID) -> OtpChallenge | None:
        """Lock and return an OTP challenge."""
        statement = (
            select(OtpChallenge)
            .where(OtpChallenge.otp_id == otp_id)
            .with_for_update()
        )
        return self.db.scalar(statement)

    def invalidate_all_by_user(self, user_id: UUID, now: datetime) -> None:
        """Invalidate every outstanding challenge owned by an account."""
        self.db.execute(
            update(OtpChallenge)
            .where(
                OtpChallenge.user_id == user_id,
                OtpChallenge.verified_at.is_(None),
                OtpChallenge.invalidated_at.is_(None),
            )
            .values(invalidated_at=now)
        )

    def flush(self) -> None:
        """Flush OTP changes without committing."""
        self.db.flush()


class AuthGrantRepository:
    """Persist and lock single-use authorization grants."""

    def __init__(self, db: Session) -> None:
        """Initialize the repository."""
        self.db = db

    def add(self, grant: AuthGrant) -> None:
        """Stage an authorization grant."""
        self.db.add(grant)

    def get_by_token_hash_for_update(
        self,
        token_hash: str,
    ) -> AuthGrant | None:
        """Lock and return a grant by protected token digest."""
        statement = (
            select(AuthGrant)
            .where(AuthGrant.token_hash == token_hash)
            .with_for_update()
        )
        return self.db.scalar(statement)

    def revoke_all_by_user(
        self,
        user_id: UUID,
        now: datetime,
        *,
        except_grant_id: UUID | None = None,
    ) -> None:
        """Revoke every outstanding grant owned by an account."""
        clauses = [
            AuthGrant.user_id == user_id,
            AuthGrant.consumed_at.is_(None),
            AuthGrant.revoked_at.is_(None),
        ]
        if except_grant_id is not None:
            clauses.append(AuthGrant.grant_id != except_grant_id)
        self.db.execute(
            update(AuthGrant).where(*clauses).values(revoked_at=now)
        )

    def revoke_subject_grants(
        self,
        user_id: UUID,
        purpose: GrantPurpose,
        subject_hash: str,
        now: datetime,
    ) -> None:
        """Revoke outstanding grants for one purpose-bound subject."""
        self.db.execute(
            update(AuthGrant)
            .where(
                AuthGrant.user_id == user_id,
                AuthGrant.purpose == purpose,
                AuthGrant.subject_hash == subject_hash,
                AuthGrant.consumed_at.is_(None),
                AuthGrant.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    def flush(self) -> None:
        """Flush grant changes without committing."""
        self.db.flush()


class SessionRepository:
    """Persist refresh generations and enforce account ownership."""

    def __init__(self, db: Session) -> None:
        """Initialize the repository."""
        self.db = db

    def add(self, session: UserSession) -> None:
        """Stage a refresh session."""
        self.db.add(session)

    def get_by_refresh_hash_for_update(
        self,
        token_hash: str,
    ) -> UserSession | None:
        """Lock and return a session by protected refresh digest."""
        statement = (
            select(UserSession)
            .where(UserSession.refresh_token_hash == token_hash)
            .with_for_update()
        )
        return self.db.scalar(statement)

    def list_active_by_user(
        self,
        user_id: UUID,
        now: datetime,
    ) -> list[UserSession]:
        """Return only live sessions owned by one account."""
        statement = (
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
                UserSession.rotated_at.is_(None),
            )
            .order_by(UserSession.issued_at.desc())
        )
        return list(self.db.scalars(statement))

    def get_owned_for_update(
        self,
        user_id: UUID,
        session_id: UUID,
    ) -> UserSession | None:
        """Lock a session only when it belongs to the account."""
        statement = (
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.session_id == session_id,
            )
            .with_for_update()
        )
        return self.db.scalar(statement)

    def get_active_owned(
        self,
        user_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> UserSession | None:
        """Return a live unrotated session owned by one account."""
        statement = select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.session_id == session_id,
            UserSession.revoked_at.is_(None),
            UserSession.rotated_at.is_(None),
            UserSession.expires_at > now,
        )
        return self.db.scalar(statement)

    def revoke_family(
        self,
        family_id: UUID,
        now: datetime,
        reason: str,
    ) -> None:
        """Revoke every live generation in one refresh family."""
        statement = (
            update(UserSession)
            .where(
                UserSession.family_id == family_id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoke_reason=reason)
        )
        self.db.execute(statement)

    def revoke_all_by_user(
        self,
        user_id: UUID,
        now: datetime,
        reason: str,
        except_session_id: UUID | None,
    ) -> None:
        """Revoke all live sessions except an optional current session."""
        clauses = [
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        ]
        if except_session_id is not None:
            clauses.append(UserSession.session_id != except_session_id)
        statement = (
            update(UserSession)
            .where(*clauses)
            .values(revoked_at=now, revoke_reason=reason)
        )
        self.db.execute(statement)

    def flush(self) -> None:
        """Flush session changes without committing."""
        self.db.flush()
