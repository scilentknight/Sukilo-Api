"""Test staff invitation acceptance and revocation."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.core.auth_context import AuthContext
from app.modules.identity.types import AccountStatus
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.invitations import VendorInvitationService, _hash_token
from app.modules.vendor.models import Vendor, VendorInvitation, VendorMember
from app.modules.vendor.types import MemberStatus, VendorStatus, VendorType

NOW = datetime(2026, 10, 28, tzinfo=UTC)
OWNER = UUID(int=3)
STAFF = UUID(int=4)
VENDOR_ID = UUID(int=5)
COUNTRY_ID = UUID(int=6)


class Account:
    """Represent one account lookup result."""

    def __init__(self, user_id: UUID, email: str, status: AccountStatus = AccountStatus.ACTIVE) -> None:
        """Initialize the account."""
        self.user_id = user_id
        self.email = email
        self.status = status


class Accounts:
    """Resolve accounts by id and email."""

    def __init__(self, *accounts: Account) -> None:
        """Initialize known accounts."""
        self.accounts = list(accounts)

    def get_by_id(self, user_id: UUID, *, for_update: bool = False) -> Account | None:
        """Return one account by id."""
        del for_update
        return next((row for row in self.accounts if row.user_id == user_id), None)

    def get_by_email(self, email: str) -> Account | None:
        """Return one account by email."""
        return next((row for row in self.accounts if row.email == email), None)


class Repository:
    """Store invitations and memberships in memory."""

    def __init__(self) -> None:
        """Initialize empty collections."""
        self.invitations: list[VendorInvitation] = []
        self.members: list[VendorMember] = []
        self.vendor = Vendor(
            vendor_id=VENDOR_ID,
            legal_name="Acme Trading",
            display_name="Acme",
            vendor_type=VendorType.COMPANY,
            registration_country_id=COUNTRY_ID,
            status=VendorStatus.ACTIVE,
            created_by_user_id=OWNER,
            version=1,
        )

    def add_invitation(self, invitation: VendorInvitation) -> None:
        """Record an invitation."""
        if invitation.invitation_id is None:
            invitation.invitation_id = uuid4()
        self.invitations.append(invitation)

    def add_member(self, member: VendorMember) -> None:
        """Record a membership."""
        self.members.append(member)

    def find_open_invitation(
        self,
        vendor_id: UUID,
        country_id: UUID,
        email: str,
        now: datetime,
    ) -> VendorInvitation | None:
        """Return an open invitation."""
        del now
        return next(
            (
                row
                for row in self.invitations
                if row.vendor_id == vendor_id
                and row.country_id == country_id
                and row.email == email
                and row.accepted_at is None
            ),
            None,
        )

    def get_invitation(self, invitation_id: UUID, *, for_update: bool = False) -> VendorInvitation | None:
        """Return one invitation."""
        del for_update
        return next((row for row in self.invitations if row.invitation_id == invitation_id), None)

    def get_member_for_user(
        self,
        vendor_id: UUID,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> VendorMember | None:
        """Return one membership."""
        del for_update
        return next(
            (row for row in self.members if row.vendor_id == vendor_id and row.user_id == user_id),
            None,
        )

    def get_member(self, vendor_member_id: UUID, *, for_update: bool = False) -> VendorMember | None:
        """Return one membership by id."""
        del for_update
        return next((row for row in self.members if row.vendor_member_id == vendor_member_id), None)

    def get_vendor(self, vendor_id: UUID, *, for_update: bool = False) -> Vendor | None:
        """Return the organization."""
        del for_update
        if vendor_id != VENDOR_ID:
            return None
        return self.vendor


class Authorizer:
    """Allow the owner permission."""

    def allows(self, actor: AuthContext, permission: str, country_id: UUID, vendor_id: UUID | None) -> bool:
        """Allow only the owner actor."""
        del permission
        return actor.user_id == OWNER and country_id == COUNTRY_ID and vendor_id == VENDOR_ID


class Grants:
    """Record staff grants."""

    def __init__(self) -> None:
        """Initialize grant records."""
        self.staff: list[tuple[UUID, UUID, UUID]] = []

    def grant_vendor_staff(
        self,
        user_id: UUID,
        vendor_id: UUID,
        country_id: UUID,
        actor_user_id: UUID,
    ) -> UUID:
        """Record a staff grant."""
        del actor_user_id
        self.staff.append((user_id, vendor_id, country_id))
        return uuid4()

    def find_active_assignment(self, *args: object) -> None:
        """Return no stored assignment."""
        del args
        return None

    def revoke_assignment(self, *args: object) -> None:
        """Record nothing."""
        del args


def _service(repository: Repository, accounts: Accounts) -> tuple[VendorInvitationService, Grants]:
    """Build an invitation service and its grant spy."""
    grants = Grants()
    service = VendorInvitationService(
        repository,
        accounts,
        object(),
        Authorizer(),
        grants,
        lambda: NOW,
    )
    return service, grants


def _owner() -> AuthContext:
    """Return the vendor owner principal."""
    return AuthContext(OWNER, uuid4(), 1, ("password",), NOW, uuid4())


def test_accept_grants_staff_for_that_vendor_and_country_only() -> None:
    """Grant VENDOR_STAFF once for the invited vendor and country."""
    repository = Repository()
    staff = Account(STAFF, "staff@example.com")
    service, grants = _service(repository, Accounts(staff))
    created = service.invite(_owner(), vendor_id=VENDOR_ID, country_id=COUNTRY_ID, email="staff@example.com")
    actor = AuthContext(STAFF, uuid4(), 1, ("password",), NOW, uuid4())
    service.accept(actor, created.invitation_id, token=created.token)
    assert grants.staff == [(STAFF, VENDOR_ID, COUNTRY_ID)]
    assert repository.members[0].status is MemberStatus.ACTIVE
    assert repository.invitations[0].token_hash == _hash_token(created.token)
    assert created.token not in repository.invitations[0].token_hash


def test_second_accept_of_the_same_token_fails() -> None:
    """Reject a second acceptance of one invitation token."""
    repository = Repository()
    staff = Account(STAFF, "staff@example.com")
    service, _grants = _service(repository, Accounts(staff))
    created = service.invite(_owner(), vendor_id=VENDOR_ID, country_id=COUNTRY_ID, email="Staff@Example.com")
    actor = AuthContext(STAFF, uuid4(), 1, ("password",), NOW, uuid4())
    service.accept(actor, created.invitation_id, token=created.token)
    with pytest.raises(VendorServiceError) as captured:
        service.accept(actor, created.invitation_id, token=created.token)
    assert captured.value.status_code == 409


def test_unknown_email_uses_the_same_invitation_shape() -> None:
    """Create an invitation when no account uses the email."""
    repository = Repository()
    service, grants = _service(repository, Accounts())
    created = service.invite(_owner(), vendor_id=VENDOR_ID, country_id=COUNTRY_ID, email="new@example.com")
    assert created.token
    assert created.expires_at == NOW + timedelta(days=7)
    assert repository.invitations[0].user_id is None
    assert repository.members == []
    assert grants.staff == []
