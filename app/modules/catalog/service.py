"""Publish shared taxonomy only through review or super-admin paths."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from app.core.auth_context import AuthContext
from app.modules.catalog.errors import CatalogServiceError
from app.modules.catalog.models import (
    Brand,
    BrandRequest,
    Category,
    CategoryChangeRequest,
    CategoryTranslation,
)
from app.modules.catalog.repository import CatalogRepository
from app.modules.catalog.types import RecordStatus, RequestStatus, ReviewDecision
from app.ports.country import CountryReader

TAXONOMY_DECIDE = "catalog.taxonomy.decide"


@dataclass(frozen=True, slots=True)
class CategoryView:
    """Expose one enabled or newly created category."""

    category_id: UUID
    parent_id: UUID | None
    code: str
    name: str | None


@dataclass(frozen=True, slots=True)
class BrandView:
    """Expose one brand name."""

    brand_id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class CategoryRequestView:
    """Expose a category proposal without publishing it."""

    request_id: UUID
    status: RequestStatus
    category_id: UUID | None


@dataclass(frozen=True, slots=True)
class BrandRequestView:
    """Expose a brand proposal and the linked brand, when one exists."""

    request_id: UUID
    status: RequestStatus
    brand_id: UUID | None


class TaxonomyAuthorizer(Protocol):
    """Decide taxonomy permissions for global and country scopes."""

    def allows(
        self,
        actor: AuthContext,
        permission: str,
        country_id: UUID,
        vendor_id: UUID | None,
    ) -> bool:
        """Return whether the actor holds the permission in scope."""
        ...

    def allows_global(self, actor: AuthContext, permission: str) -> bool:
        """Return whether the actor holds a global permission."""
        ...


class SellerApprovalReader(Protocol):
    """Report whether a vendor may sell in one country."""

    def is_approved(self, vendor_id: UUID, country_id: UUID) -> bool:
        """Return whether the vendor country application is approved."""
        ...


class StepUpConsumer(Protocol):
    """Consume one step-up grant for a taxonomy mutation."""

    def __call__(self, token: str, actor: AuthContext, action_class: str) -> None:
        """Consume the grant or raise when it is invalid."""
        ...


class AttributeLink:
    """Describe one attribute attached to a category."""

    def __init__(self, attribute_id: UUID, required: bool) -> None:
        """Initialize one link."""
        self.attribute_id = attribute_id
        self.required = required


class CatalogTaxonomyService:
    """Create canonical taxonomy and country enablement."""

    def __init__(
        self,
        repository: CatalogRepository,
        countries: CountryReader,
        authorizer: TaxonomyAuthorizer,
        sellers: SellerApprovalReader,
        consume_step_up: StepUpConsumer,
    ) -> None:
        """Initialize taxonomy dependencies."""
        self.repository = repository
        self.countries = countries
        self.authorizer = authorizer
        self.sellers = sellers
        self.consume_step_up = consume_step_up

    def list_categories(self, country_code: str, locale_code: str) -> list[CategoryView]:
        """Return enabled active categories for one country and locale."""
        country_id = _country_id(self.countries, country_code)
        self._require_locale(country_id, locale_code)
        views: list[CategoryView] = []
        for category in self.repository.list_enabled_categories(country_id):
            if not _ancestors_active(self.repository, category):
                continue
            translation = self.repository.get_translation(category.category_id, locale_code.strip())
            name = None if translation is None else translation.name
            views.append(CategoryView(category.category_id, category.parent_id, category.code, name))
        return views

    def create_category(
        self,
        actor: AuthContext,
        *,
        code: str,
        parent_id: UUID | None,
        locale_code: str,
        name: str,
    ) -> CategoryView:
        """Create a canonical category that is disabled in every country."""
        self._require_global(actor, "catalog.category.write")
        normalized = normalize_code(code)
        if self.repository.find_category_by_code(normalized) is not None:
            raise CatalogServiceError(409, "Category code is already used.", code="CODE_TAKEN")
        self._require_parent(parent_id)
        category = Category(
            category_id=uuid4(),
            parent_id=parent_id,
            code=normalized,
            status=RecordStatus.ACTIVE,
        )
        self.repository.add_category(category)
        translation = self._translation(category.category_id, locale_code, name)
        self.repository.add_translation(translation)
        return CategoryView(category.category_id, category.parent_id, category.code, translation.name)

    def enable_category(
        self,
        actor: AuthContext,
        category_id: UUID,
        *,
        country_code: str,
        step_up_token: str,
    ) -> None:
        """Enable one active category in the reviewer's country."""
        country_id = _country_id(self.countries, country_code)
        self._require_country(actor, "catalog.category.enable", country_id)
        category = self.repository.get_category(category_id, for_update=True)
        if category is None:
            raise CatalogServiceError(404, "Category was not found.")
        if category.status is not RecordStatus.ACTIVE:
            raise CatalogServiceError(409, "Category cannot be enabled.", code="CATEGORY_RETIRED")
        self._consume(step_up_token, actor)
        self.repository.enable_category(category.category_id, country_id)

    def request_category(
        self,
        actor: AuthContext,
        *,
        vendor_id: UUID,
        country_code: str,
        proposed_code: str,
        proposed_parent_id: UUID | None,
        category_id: UUID | None,
    ) -> CategoryRequestView:
        """Store a category proposal without inserting a category."""
        country_id = _country_id(self.countries, country_code)
        self._require_seller(actor, vendor_id, country_id)
        normalized = normalize_code(proposed_code)
        if category_id is None and self.repository.find_category_by_code(normalized) is not None:
            raise CatalogServiceError(409, "Category code is already used.", code="CODE_TAKEN")
        if category_id is not None and self.repository.get_category(category_id) is None:
            raise CatalogServiceError(404, "Category was not found.")
        request = CategoryChangeRequest(
            request_id=uuid4(),
            category_id=category_id,
            proposed_parent_id=proposed_parent_id,
            proposed_code=normalized,
            country_id=country_id,
            vendor_id=vendor_id,
            requested_by=actor.user_id,
            status=RequestStatus.PENDING,
        )
        self.repository.add_category_request(request)
        return CategoryRequestView(request.request_id, request.status, request.category_id)

    def decide_category(
        self,
        actor: AuthContext,
        request_id: UUID,
        *,
        decision: ReviewDecision,
        step_up_token: str,
    ) -> CategoryRequestView:
        """Approve or reject a category proposal."""
        request = self._pending_category_request(request_id)
        self._require_country(actor, "catalog.category.enable", request.country_id)
        if decision is ReviewDecision.REJECTED:
            self._consume(step_up_token, actor)
            return _reject_category(request, actor.user_id)
        self._consume(step_up_token, actor)
        self._apply_category(request)
        request.status = RequestStatus.APPROVED
        request.reviewed_by = actor.user_id
        return CategoryRequestView(request.request_id, request.status, request.category_id)

    def list_brands(self, country_code: str) -> list[BrandView]:
        """Return enabled active brands for one country."""
        country_id = _country_id(self.countries, country_code)
        return [
            BrandView(brand.brand_id, brand.name)
            for brand in self.repository.list_enabled_brands(country_id)
        ]

    def request_brand(
        self,
        actor: AuthContext,
        *,
        vendor_id: UUID,
        country_code: str,
        proposed_name: str,
    ) -> BrandRequestView:
        """Store a brand proposal without inserting a brand."""
        country_id = _country_id(self.countries, country_code)
        self._require_seller(actor, vendor_id, country_id)
        request = BrandRequest(
            request_id=uuid4(),
            proposed_name=_required_text(proposed_name, "Brand name is empty."),
            country_id=country_id,
            vendor_id=vendor_id,
            requested_by=actor.user_id,
            status=RequestStatus.PENDING,
        )
        self.repository.add_brand_request(request)
        return BrandRequestView(request.request_id, request.status, None)

    def decide_brand(
        self,
        actor: AuthContext,
        request_id: UUID,
        *,
        decision: ReviewDecision,
        link_brand_id: UUID | None,
        step_up_token: str,
    ) -> BrandRequestView:
        """Approve a brand by creating it or linking a confirmed existing brand."""
        request = self._pending_brand_request(request_id)
        self._require_country(actor, "catalog.brand.review", request.country_id)
        if decision is ReviewDecision.REJECTED:
            self._consume(step_up_token, actor)
            request.status = RequestStatus.REJECTED
            request.reviewed_by = actor.user_id
            return BrandRequestView(request.request_id, request.status, request.brand_id)
        brand = self._brand_for_approval(request, link_brand_id)
        self._consume(step_up_token, actor)
        if self.repository.get_brand(brand.brand_id) is None:
            self.repository.add_brand(brand)
        self.repository.enable_brand(brand.brand_id, request.country_id)
        request.brand_id = brand.brand_id
        request.status = RequestStatus.APPROVED
        request.reviewed_by = actor.user_id
        return BrandRequestView(request.request_id, request.status, request.brand_id)

    def replace_attributes(
        self,
        actor: AuthContext,
        category_id: UUID,
        links: list[AttributeLink],
    ) -> int:
        """Replace the attribute set stored for one category."""
        self._require_global(actor, "catalog.category.write")
        if self.repository.get_category(category_id) is None:
            raise CatalogServiceError(404, "Category was not found.")
        seen: set[UUID] = set()
        pairs: list[tuple[UUID, bool]] = []
        for link in links:
            if link.attribute_id in seen:
                raise CatalogServiceError(422, "Attribute is duplicated.")
            seen.add(link.attribute_id)
            attribute = self.repository.get_attribute(link.attribute_id)
            if attribute is None or attribute.status is not RecordStatus.ACTIVE:
                raise CatalogServiceError(404, "Attribute was not found.")
            pairs.append((link.attribute_id, link.required))
        self.repository.replace_category_attributes(category_id, pairs)
        return len(pairs)

    def _require_global(self, actor: AuthContext, permission: str) -> None:
        """Reject an actor who lacks a global taxonomy permission."""
        if not self.authorizer.allows_global(actor, permission):
            raise CatalogServiceError(403, "Taxonomy action is outside the assigned scope.")

    def _require_country(self, actor: AuthContext, permission: str, country_id: UUID) -> None:
        """Reject an actor who lacks the permission in this country."""
        if not self.authorizer.allows(actor, permission, country_id, None):
            raise CatalogServiceError(403, "Taxonomy action is outside the assigned country.")

    def _require_seller(self, actor: AuthContext, vendor_id: UUID, country_id: UUID) -> None:
        """Reject a seller who is not an approved owner in the country."""
        if not self.authorizer.allows(actor, "catalog.taxonomy.request", country_id, vendor_id):
            raise CatalogServiceError(403, "Taxonomy request is outside the assigned country.")
        if not self.sellers.is_approved(vendor_id, country_id):
            raise CatalogServiceError(409, "Seller country is not approved.", code="APPLICATION_NOT_APPROVED")

    def _require_locale(self, country_id: UUID, locale_code: str) -> None:
        """Reject a locale the country has not enabled."""
        if not self.countries.is_locale_enabled(country_id, locale_code.strip()):
            raise CatalogServiceError(422, "Locale is not enabled.", code="LOCALE_UNAVAILABLE")

    def _require_parent(self, parent_id: UUID | None) -> None:
        """Reject a missing or retired parent category."""
        if parent_id is None:
            return
        parent = self.repository.get_category(parent_id)
        if parent is None:
            raise CatalogServiceError(404, "Parent category was not found.")
        if parent.status is not RecordStatus.ACTIVE:
            raise CatalogServiceError(409, "Parent category is retired.", code="PARENT_RETIRED")

    def _translation(self, category_id: UUID, locale_code: str, name: str) -> CategoryTranslation:
        """Build one category translation after locale and name checks."""
        cleaned_locale = locale_code.strip()
        if not cleaned_locale:
            raise CatalogServiceError(422, "Locale is not enabled.", code="LOCALE_UNAVAILABLE")
        return CategoryTranslation(
            category_id=category_id,
            locale_code=cleaned_locale,
            name=_required_text(name, "Category name is empty."),
        )

    def _consume(self, token: str, actor: AuthContext) -> None:
        """Consume the taxonomy step-up grant."""
        self.consume_step_up(token, actor, TAXONOMY_DECIDE)

    def _pending_category_request(self, request_id: UUID) -> CategoryChangeRequest:
        """Lock one open category request."""
        request = self.repository.get_category_request(request_id, for_update=True)
        if request is None:
            raise CatalogServiceError(404, "Category request was not found.")
        if request.status is not RequestStatus.PENDING:
            raise CatalogServiceError(409, "Category request is closed.", code="REQUEST_CLOSED")
        return request

    def _pending_brand_request(self, request_id: UUID) -> BrandRequest:
        """Lock one open brand request."""
        request = self.repository.get_brand_request(request_id, for_update=True)
        if request is None:
            raise CatalogServiceError(404, "Brand request was not found.")
        if request.status is not RequestStatus.PENDING:
            raise CatalogServiceError(409, "Brand request is closed.", code="REQUEST_CLOSED")
        return request

    def _apply_category(self, request: CategoryChangeRequest) -> None:
        """Insert a new category or keep an existing code unchanged."""
        if request.category_id is None:
            self._insert_requested_category(request)
            return
        category = self.repository.get_category(request.category_id, for_update=True)
        if category is None:
            raise CatalogServiceError(404, "Category was not found.")
        if request.proposed_code != category.code:
            raise CatalogServiceError(409, "Category code is immutable.", code="CODE_IMMUTABLE")
        if request.proposed_parent_id == category.category_id:
            raise CatalogServiceError(422, "Category parent is invalid.")
        if request.proposed_parent_id is not None:
            self._require_parent(request.proposed_parent_id)
            category.parent_id = request.proposed_parent_id

    def _insert_requested_category(self, request: CategoryChangeRequest) -> None:
        """Insert a canonical category and leave it disabled."""
        if self.repository.find_category_by_code(request.proposed_code) is not None:
            raise CatalogServiceError(409, "Category code is already used.", code="CODE_TAKEN")
        self._require_parent(request.proposed_parent_id)
        category = Category(
            category_id=uuid4(),
            parent_id=request.proposed_parent_id,
            code=request.proposed_code,
            status=RecordStatus.ACTIVE,
        )
        self.repository.add_category(category)
        request.category_id = category.category_id

    def _brand_for_approval(self, request: BrandRequest, link_brand_id: UUID | None) -> Brand:
        """Return the confirmed brand, or a new unsaved brand when none exists."""
        normalized = normalize_brand_name(request.proposed_name)
        existing = self.repository.find_brand_by_normalized_name(normalized)
        if existing is None:
            if link_brand_id is not None:
                raise CatalogServiceError(409, "Brand link does not match.", code="BRAND_LINK_MISMATCH")
            return Brand(
                brand_id=uuid4(),
                name=request.proposed_name.strip(),
                normalized_name=normalized,
                status=RecordStatus.ACTIVE,
            )
        if link_brand_id is None:
            raise CatalogServiceError(409, "Brand link must be confirmed.", code="BRAND_LINK_REQUIRED")
        if link_brand_id != existing.brand_id:
            raise CatalogServiceError(409, "Brand link does not match.", code="BRAND_LINK_MISMATCH")
        if existing.status is not RecordStatus.ACTIVE:
            raise CatalogServiceError(409, "Brand cannot be enabled.", code="BRAND_RETIRED")
        return existing


def normalize_code(value: str) -> str:
    """Normalize a category code to lowercase ASCII words.

    Args:
        value: Caller-supplied code.

    Returns:
        Hyphenated lowercase code.

    Raises:
        CatalogServiceError: If the code has no usable characters.
    """
    return _normalize(value, "-", "Category code is empty.")


def normalize_brand_name(value: str) -> str:
    """Normalize a brand name so spellings share one key.

    Args:
        value: Caller-supplied brand name.

    Returns:
        Lowercase brand key.

    Raises:
        CatalogServiceError: If the name has no usable characters.
    """
    return _normalize(value, " ", "Brand name is empty.")


def _normalize(value: str, separator: str, message: str) -> str:
    """Join ASCII words with one separator."""
    pieces: list[str] = []
    current: list[str] = []
    for char in value.strip().lower():
        if char.isascii() and char.isalnum():
            current.append(char)
            continue
        if current:
            pieces.append("".join(current))
            current = []
    if current:
        pieces.append("".join(current))
    normalized = separator.join(pieces)
    if not normalized:
        raise CatalogServiceError(422, message)
    return normalized


def _country_id(countries: CountryReader, country_code: str) -> UUID:
    """Resolve a country code to its identifier."""
    try:
        return countries.resolve_country(country_code).country_id
    except (KeyError, ValueError, UnicodeError) as exc:
        raise CatalogServiceError(422, "Country code is invalid.") from exc


def _required_text(value: str, message: str) -> str:
    """Return stripped text or reject a blank value."""
    cleaned = value.strip()
    if not cleaned:
        raise CatalogServiceError(422, message)
    return cleaned


def _ancestors_active(repository: CatalogRepository, category: Category) -> bool:
    """Return whether every parent of a category is active."""
    seen: set[UUID] = set()
    parent_id = category.parent_id
    while parent_id is not None:
        if parent_id in seen:
            return False
        seen.add(parent_id)
        parent = repository.get_category(parent_id)
        if parent is None or parent.status is not RecordStatus.ACTIVE:
            return False
        parent_id = parent.parent_id
    return True


def _reject_category(request: CategoryChangeRequest, reviewer_id: UUID) -> CategoryRequestView:
    """Mark one category request rejected."""
    request.status = RequestStatus.REJECTED
    request.reviewed_by = reviewer_id
    return CategoryRequestView(request.request_id, request.status, request.category_id)
