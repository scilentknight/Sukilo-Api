"""Invite, accept, and revoke vendor staff."""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.core.auth_context import AuthContext
from app.core.identifiers import IdentifierValidationError, normalize_email
from app.modules.access.vendor_grants import VendorAuthorizer, VendorRoleGranter
from app.modules.identity.types import AccountStatus
from app.modules.vendor.applications import AccountReader
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.models import VendorInvitation, VendorMember
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.types import MemberStatus
from app.ports.country import CountryReader

_INVITE_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class InvitationView:
    """Expose a new invitation and its one-time raw token."""

    invitation_id: UUID
    expires_at: datetime
    token: str


class VendorInvitationService:
    """Create staff access only after the invited account accepts."""

    def __init__(
        self,
        repository: VendorRepository,
        accounts: AccountReader,
        countries: CountryReader,
        authorizer: VendorAuthorizer,
        grants: VendorRoleGranter,
        clock: Callable[[], datetime],
    ) -> None:
        """Initialize invitation dependencies."""
        self.repository = repository
        self.accounts = accounts
        self.countries = countries
        self.authorizer = authorizer
        self.grants = grants
        self.clock = clock

    def invite(
        self,
        actor: AuthContext,
        *,
        vendor_id: UUID,
        country_id: UUID,
        email: str,
    ) -> InvitationView:
        """Invite an email without revealing whether an account exists."""
        self._require_owner(actor, vendor_id, country_id, "vendor.staff.invite")
        try:
            normalized = normalize_email(email)
        except IdentifierValidationError as exc:
            raise VendorServiceError(422, "Email address is invalid.") from exc
        now = self.clock()
        if self.repository.find_open_invitation(vendor_id, country_id, normalized, now):
            raise VendorServiceError(409, "An invitation is already open.")
        account = self.accounts.get_by_email(normalized)
        user_id = getattr(account, "user_id", None)
        if account is not None and getattr(account, "status", None) != AccountStatus.ACTIVE:
            user_id = None
        token = secrets.token_urlsafe(32)
        expires_at = now + _INVITE_TTL
        invitation = VendorInvitation(
            vendor_id=vendor_id,
            country_id=country_id,
            email=normalized,
            user_id=user_id,
            token_hash=_hash_token(token),
            expires_at=expires_at,
            created_by=actor.user_id,
        )
        self.repository.add_invitation(invitation)
        if user_id is not None and self.repository.get_member_for_user(vendor_id, user_id) is None:
            self.repository.add_member(
                VendorMember(
                    vendor_id=vendor_id,
                    user_id=user_id,
                    status=MemberStatus.INVITED,
                    invited_at=now,
                )
            )
        return InvitationView(invitation.invitation_id, expires_at, token)

    def invite_by_code(
        self,
        actor: AuthContext,
        *,
        vendor_id: UUID,
        country_code: str,
        email: str,
    ) -> InvitationView:
        """Resolve the market code and invite staff for that country."""
        try:
            country = self.countries.resolve_country(country_code)
        except (KeyError, ValueError, UnicodeError) as exc:
            raise VendorServiceError(422, "Country is unavailable.") from exc
        return self.invite(
            actor,
            vendor_id=vendor_id,
            country_id=country.country_id,
            email=email,
        )

    def accept(
        self,
        actor: AuthContext,
        invitation_id: UUID,
        *,
        token: str,
    ) -> UUID:
        """Accept one invitation and grant staff access for that country."""
        invitation = self.repository.get_invitation(invitation_id, for_update=True)
        if invitation is None or not hmac.compare_digest(invitation.token_hash, _hash_token(token)):
            raise VendorServiceError(404, "Invitation is unavailable.")
        if invitation.revoked_at is not None or invitation.accepted_at is not None:
            raise VendorServiceError(409, "Invitation can no longer be accepted.")
        now = self.clock()
        if invitation.expires_at <= now:
            raise VendorServiceError(409, "Invitation can no longer be accepted.")
        account = self.accounts.get_by_id(actor.user_id, for_update=True)
        if account is None or getattr(account, "status", None) != AccountStatus.ACTIVE:
            raise VendorServiceError(403, "Account cannot accept the invitation.")
        email = getattr(account, "email", None)
        if invitation.user_id is not None and invitation.user_id != actor.user_id:
            raise VendorServiceError(404, "Invitation is unavailable.")
        if invitation.user_id is None and email != invitation.email:
            raise VendorServiceError(404, "Invitation is unavailable.")
        invitation.user_id = actor.user_id
        invitation.accepted_at = now
        member = self.repository.get_member_for_user(
            invitation.vendor_id,
            actor.user_id,
            for_update=True,
        )
        if member is None:
            member = VendorMember(
                vendor_id=invitation.vendor_id,
                user_id=actor.user_id,
                status=MemberStatus.ACTIVE,
                invited_at=now,
                accepted_at=now,
            )
            self.repository.add_member(member)
        else:
            member.status = MemberStatus.ACTIVE
            member.accepted_at = now
        self.grants.grant_vendor_staff(
            actor.user_id,
            invitation.vendor_id,
            invitation.country_id,
            invitation.created_by,
        )
        return invitation.invitation_id

    def revoke_by_code(
        self,
        actor: AuthContext,
        vendor_member_id: UUID,
        *,
        country_code: str,
        reason: str,
    ) -> None:
        """Resolve the market code and revoke staff in that country."""
        try:
            country = self.countries.resolve_country(country_code)
        except (KeyError, ValueError, UnicodeError) as exc:
            raise VendorServiceError(422, "Country is unavailable.") from exc
        self.revoke(
            actor,
            vendor_member_id,
            country_id=country.country_id,
            reason=reason,
        )

    def revoke(
        self,
        actor: AuthContext,
        vendor_member_id: UUID,
        *,
        country_id: UUID,
        reason: str,
    ) -> None:
        """Suspend a staff member and revoke that country badge."""
        cleaned = reason.strip()
        if not cleaned:
            raise VendorServiceError(422, "Revocation reason is required.")
        member = self.repository.get_member(vendor_member_id, for_update=True)
        if member is None:
            raise VendorServiceError(404, "Membership is unavailable.")
        vendor = self.repository.get_vendor(member.vendor_id)
        if vendor is None:
            raise VendorServiceError(404, "Membership is unavailable.")
        if member.user_id == vendor.created_by_user_id:
            raise VendorServiceError(403, "The organization owner cannot be revoked here.")
        self._require_owner(actor, member.vendor_id, country_id, "vendor.staff.revoke")
        assignment_id = self.grants.find_active_assignment(
            member.user_id,
            member.vendor_id,
            country_id,
            "VENDOR_STAFF",
        )
        member.status = MemberStatus.SUSPENDED
        if assignment_id is not None:
            self.grants.revoke_assignment(assignment_id, actor.user_id, cleaned)

    def _require_owner(
        self,
        actor: AuthContext,
        vendor_id: UUID,
        country_id: UUID,
        permission: str,
    ) -> None:
        """Require the owner badge for this vendor and country."""
        if not self.authorizer.allows(actor, permission, country_id, vendor_id):
            raise VendorServiceError(403, "Staff management is outside this vendor.")


def _hash_token(token: str) -> str:
    """Hash an invitation token so the raw value is not stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
