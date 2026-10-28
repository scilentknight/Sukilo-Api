"""Review one seller country application inside that country."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.access.vendor_grants import VendorAuthorizer, VendorRoleGranter
from app.modules.identity.types import AccountStatus
from app.modules.vendor.applications import AccountReader, ApplicationView
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.models import VendorMember
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.types import MemberStatus, OnboardingStatus, VendorStatus
from app.ports.audit import AuditEventInput, AuditWriter, NoOpAuditWriter
from app.ports.country import CountryReader

_DECIDABLE = frozenset({OnboardingStatus.SUBMITTED, OnboardingStatus.REVIEW})


class StepUpConsumer(Protocol):
    """Consume one step-up grant for a review mutation."""

    def __call__(self, token: str, actor: AuthContext, action_class: str) -> None:
        """Consume the grant or raise when it is invalid."""
        ...


class VendorReviewService:
    """Approve, reject, or return one country application."""

    def __init__(
        self,
        repository: VendorRepository,
        accounts: AccountReader,
        countries: CountryReader,
        authorizer: VendorAuthorizer,
        grants: VendorRoleGranter,
        audit: AuditWriter,
        transaction: DatabaseTransaction,
        clock: Callable[[], datetime],
        consume_step_up: StepUpConsumer,
    ) -> None:
        """Initialize review dependencies."""
        self.repository = repository
        self.accounts = accounts
        self.countries = countries
        self.authorizer = authorizer
        self.grants = grants
        self.audit = audit
        self.transaction = transaction
        self.clock = clock
        self.consume_step_up = consume_step_up

    def list_queue(
        self,
        actor: AuthContext,
        country_id: UUID,
    ) -> list[ApplicationView]:
        """List submitted and in-review applications for one market."""
        if not self.authorizer.allows(
            actor,
            "vendor.application.review",
            country_id,
            None,
        ):
            raise VendorServiceError(403, "Review is outside the assigned country.")
        views: list[ApplicationView] = []
        for country in self.repository.list_countries_for_review(country_id):
            vendor = self.repository.get_vendor(country.vendor_id)
            if vendor is None:
                continue
            views.append(_queue_view(vendor, country))
        return views

    def list_for_country_code(
        self,
        actor: AuthContext,
        country_code: str,
    ) -> list[ApplicationView]:
        """Resolve a market code and list that country's review queue."""
        try:
            country = self.countries.resolve_country(country_code)
        except (KeyError, ValueError, UnicodeError) as exc:
            raise VendorServiceError(422, "Country is unavailable.") from exc
        return self.list_queue(actor, country.country_id)

    def approve(
        self,
        actor: AuthContext,
        vendor_country_id: UUID,
        *,
        step_up_token: str,
    ) -> ApplicationView:
        """Approve one application and grant its owner badge."""
        self._require_audit()
        self.consume_step_up(step_up_token, actor, "vendor.application.approve")
        country = self._lock_decidable(vendor_country_id)
        vendor = self._vendor(country.vendor_id)
        self._deny_self(actor, vendor.created_by_user_id)
        self._require_review_permission(actor, "vendor.application.approve", country)
        applicant = self.accounts.get_by_id(vendor.created_by_user_id, for_update=True)
        if applicant is None or getattr(applicant, "status", None) != AccountStatus.ACTIVE:
            raise VendorServiceError(403, "Applicant account cannot be approved.")
        now = self.clock()
        country.onboarding_status = OnboardingStatus.APPROVED
        country.approved_at = now
        country.approved_by = actor.user_id
        country.selling_enabled = False
        country.payout_enabled = False
        country.version += 1
        if vendor.status is VendorStatus.PENDING:
            vendor.status = VendorStatus.ACTIVE
            vendor.version += 1
        self.repository.add_member(
            VendorMember(
                vendor_id=vendor.vendor_id,
                user_id=vendor.created_by_user_id,
                status=MemberStatus.ACTIVE,
                accepted_at=now,
            )
        )
        self.grants.grant_vendor_owner(
            vendor.created_by_user_id,
            vendor.vendor_id,
            country.country_id,
            actor.user_id,
        )
        self._stage(actor, country.vendor_country_id, country.country_id, vendor.vendor_id, "APPROVED")
        return _queue_view(vendor, country)

    def reject(
        self,
        actor: AuthContext,
        vendor_country_id: UUID,
        *,
        reason: str,
        step_up_token: str,
    ) -> ApplicationView:
        """Reject one application without granting a badge."""
        self._require_audit()
        cleaned = reason.strip()
        if not cleaned:
            raise VendorServiceError(422, "Rejection reason is required.")
        self.consume_step_up(step_up_token, actor, "vendor.application.reject")
        country = self._lock_decidable(vendor_country_id)
        vendor = self._vendor(country.vendor_id)
        self._require_review_permission(actor, "vendor.application.approve", country)
        country.onboarding_status = OnboardingStatus.REJECTED
        country.version += 1
        self._stage(
            actor,
            country.vendor_country_id,
            country.country_id,
            vendor.vendor_id,
            "REJECTED",
            cleaned,
        )
        return _queue_view(vendor, country)

    def request_info(
        self,
        actor: AuthContext,
        vendor_country_id: UUID,
        *,
        reason: str,
        step_up_token: str,
    ) -> ApplicationView:
        """Return an application to review so the applicant can edit it."""
        self._require_audit()
        cleaned = reason.strip()
        if not cleaned:
            raise VendorServiceError(422, "Review reason is required.")
        self.consume_step_up(step_up_token, actor, "vendor.application.review")
        country = self._lock_decidable(vendor_country_id)
        vendor = self._vendor(country.vendor_id)
        self._require_review_permission(actor, "vendor.application.review", country)
        country.onboarding_status = OnboardingStatus.REVIEW
        country.version += 1
        self._stage(
            actor,
            country.vendor_country_id,
            country.country_id,
            vendor.vendor_id,
            "REVIEW",
            cleaned,
        )
        return _queue_view(vendor, country)

    def _require_audit(self) -> None:
        """Refuse approval evidence that would be discarded."""
        if isinstance(self.audit, NoOpAuditWriter):
            raise VendorServiceError(503, "Audit writer is unavailable.")

    def _lock_decidable(self, vendor_country_id: UUID):
        """Lock an application that is waiting for a decision."""
        country = self.repository.get_country(vendor_country_id, for_update=True)
        if country is None:
            raise VendorServiceError(404, "Application is unavailable.")
        if country.onboarding_status not in _DECIDABLE:
            raise VendorServiceError(409, "Application cannot be decided.")
        return country

    def _vendor(self, vendor_id: UUID):
        """Lock the organization for a decision."""
        vendor = self.repository.get_vendor(vendor_id, for_update=True)
        if vendor is None:
            raise VendorServiceError(404, "Application is unavailable.")
        return vendor

    def _deny_self(self, actor: AuthContext, applicant_id: UUID) -> None:
        """Stop an applicant from approving their own application."""
        if actor.user_id == applicant_id:
            raise VendorServiceError(403, "Applicant cannot approve the application.")

    def _require_review_permission(self, actor: AuthContext, permission: str, country) -> None:
        """Require a reviewer assignment for the application country."""
        if not self.authorizer.allows(actor, permission, country.country_id, country.vendor_id):
            raise VendorServiceError(403, "Review is outside the assigned country.")

    def _stage(
        self,
        actor: AuthContext,
        resource_id: UUID,
        country_id: UUID,
        vendor_id: UUID,
        decision: str,
        reason: str | None = None,
    ) -> None:
        """Stage a decision audit event without document numbers."""
        after = {"decision": decision}
        if reason is not None:
            after["reason"] = reason
        self.audit.stage(
            AuditEventInput(
                action=f"vendor.application.{decision.lower()}",
                resource_type="vendor.vendor_country",
                resource_id=resource_id,
                correlation_id=actor.correlation_id,
                actor_user_id=actor.user_id,
                country_id=country_id,
                vendor_id=vendor_id,
                after_data=after,
            ),
            self.transaction,
        )


def _queue_view(vendor, country) -> ApplicationView:
    """Build a review view without loading document numbers."""
    return ApplicationView(
        vendor_id=vendor.vendor_id,
        vendor_country_id=country.vendor_country_id,
        country_id=country.country_id,
        legal_name=vendor.legal_name,
        display_name=vendor.display_name,
        vendor_type=vendor.vendor_type,
        vendor_status=vendor.status,
        onboarding_status=country.onboarding_status,
        selling_enabled=country.selling_enabled,
        payout_enabled=country.payout_enabled,
        version=country.version,
        documents=(),
    )
