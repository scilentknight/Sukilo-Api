"""Persist identity accounts with parameterized SQLAlchemy statements."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.identity.models import AuthIdentity, UserAccount


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

    def get_by_email(self, email: str) -> UserAccount | None:
        """Return an account by normalized email.

        Args:
            email: Normalized email address.

        Returns:
            Matching account, or `None`.
        """
        statement = select(UserAccount).where(UserAccount.email == email)
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
