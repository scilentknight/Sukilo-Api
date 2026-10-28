"""Test taxonomy publication rules."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.auth_context import AuthContext
from app.modules.catalog.errors import CatalogServiceError
from app.modules.catalog.models import (
    AttributeDefinition,
    Brand,
    BrandCountry,
    BrandRequest,
    Category,
    CategoryAttribute,
    CategoryChangeRequest,
    CategoryCountry,
    CategoryTranslation,
)
from app.modules.catalog.schemas import BrandRequestCreate
from app.modules.catalog.service import (
    TAXONOMY_DECIDE,
    AttributeLink,
    CatalogTaxonomyService,
)
from app.modules.catalog.types import (
    AttributeDataType,
    RecordStatus,
    RequestStatus,
    ReviewDecision,
)
from app.ports.country import CountryReference

NOW = datetime(2026, 10, 28, tzinfo=UTC)
ADMIN = UUID(int=1)
REVIEWER = UUID(int=2)
OWNER = UUID(int=3)
VENDOR = UUID(int=4)
NEPAL = UUID(int=10)
UAE = UUID(int=20)


class Countries:
    """Resolve Nepal and UAE for taxonomy tests."""

    def resolve_country(self, code: str) -> CountryReference:
        """Return a fixed country reference."""
        normalized = code.upper()
        country_id = NEPAL if normalized == "NP" else UAE
        return CountryReference(country_id, normalized, "ACTIVE")

    def is_locale_enabled(self, country_id: UUID, locale_code: str) -> bool:
        """Allow the locales used by the tests."""
        del country_id, locale_code
        return True


class Authorizer:
    """Allow super admin globally and Nepal reviewers or owners."""

    def allows_global(self, actor: AuthContext, permission: str) -> bool:
        """Allow only the super admin to write canonical taxonomy."""
        return actor.user_id == ADMIN and permission == "catalog.category.write"

    def allows(
        self,
        actor: AuthContext,
        permission: str,
        country_id: UUID,
        vendor_id: UUID | None,
    ) -> bool:
        """Allow Nepal review and the approved owner's requests."""
        if country_id != NEPAL:
            return False
        if actor.user_id == ADMIN:
            return True
        if actor.user_id == REVIEWER:
            return vendor_id is None and permission in {
                "catalog.category.enable",
                "catalog.brand.review",
            }
        return (
            actor.user_id == OWNER
            and vendor_id == VENDOR
            and permission == "catalog.taxonomy.request"
        )


class Sellers:
    """Treat one vendor country as approved."""

    def is_approved(self, vendor_id: UUID, country_id: UUID) -> bool:
        """Return the configured approval."""
        return vendor_id == VENDOR and country_id == NEPAL


class Consume:
    """Record step-up consumption."""

    def __init__(self) -> None:
        """Initialize an empty call list."""
        self.calls: list[str] = []

    def __call__(self, token: str, actor: AuthContext, action_class: str) -> None:
        """Record a grant or fail a bad token before any write."""
        del actor
        if token == "bad":
            raise RuntimeError("step-up")
        self.calls.append(action_class)


class Repository:
    """Store taxonomy rows in memory."""

    def __init__(self) -> None:
        """Initialize empty collections."""
        self.categories: list[Category] = []
        self.translations: list[CategoryTranslation] = []
        self.category_countries: list[CategoryCountry] = []
        self.category_requests: list[CategoryChangeRequest] = []
        self.attributes: list[AttributeDefinition] = []
        self.attribute_links: list[CategoryAttribute] = []
        self.brands: list[Brand] = []
        self.brand_countries: list[BrandCountry] = []
        self.brand_requests: list[BrandRequest] = []

    def add_category(self, category: Category) -> None:
        """Record a category."""
        self.categories.append(category)

    def get_category(self, category_id: UUID, *, for_update: bool = False) -> Category | None:
        """Return one category."""
        del for_update
        return next((row for row in self.categories if row.category_id == category_id), None)

    def find_category_by_code(self, code: str) -> Category | None:
        """Return a category with the code."""
        return next((row for row in self.categories if row.code == code), None)

    def add_translation(self, translation: CategoryTranslation) -> None:
        """Record a translation."""
        self.translations.append(translation)

    def get_translation(self, category_id: UUID, locale_code: str) -> CategoryTranslation | None:
        """Return one translation."""
        return next(
            (
                row
                for row in self.translations
                if row.category_id == category_id and row.locale_code == locale_code
            ),
            None,
        )

    def list_enabled_categories(self, country_id: UUID) -> list[Category]:
        """Return categories enabled in one country."""
        enabled = {
            row.category_id
            for row in self.category_countries
            if row.country_id == country_id and row.enabled
        }
        return [
            row
            for row in self.categories
            if row.category_id in enabled and row.status is RecordStatus.ACTIVE
        ]

    def enable_category(self, category_id: UUID, country_id: UUID) -> None:
        """Enable one category in one country."""
        self._enable(self.category_countries, CategoryCountry, "category_id", category_id, country_id)

    def add_category_request(self, request: CategoryChangeRequest) -> None:
        """Record a category request."""
        self.category_requests.append(request)

    def get_category_request(
        self,
        request_id: UUID,
        *,
        for_update: bool = False,
    ) -> CategoryChangeRequest | None:
        """Return one category request."""
        del for_update
        return next((row for row in self.category_requests if row.request_id == request_id), None)

    def get_attribute(self, attribute_id: UUID) -> AttributeDefinition | None:
        """Return one attribute."""
        return next((row for row in self.attributes if row.attribute_id == attribute_id), None)

    def replace_category_attributes(self, category_id: UUID, links: list[tuple[UUID, bool]]) -> None:
        """Replace attribute links."""
        self.attribute_links = [
            row for row in self.attribute_links if row.category_id != category_id
        ]
        for attribute_id, required in links:
            self.attribute_links.append(
                CategoryAttribute(
                    category_id=category_id,
                    attribute_id=attribute_id,
                    required=required,
                )
            )

    def add_brand(self, brand: Brand) -> None:
        """Record a brand."""
        self.brands.append(brand)

    def get_brand(self, brand_id: UUID, *, for_update: bool = False) -> Brand | None:
        """Return one brand."""
        del for_update
        return next((row for row in self.brands if row.brand_id == brand_id), None)

    def find_brand_by_normalized_name(self, normalized_name: str) -> Brand | None:
        """Return a brand with the normalized name."""
        return next((row for row in self.brands if row.normalized_name == normalized_name), None)

    def list_enabled_brands(self, country_id: UUID) -> list[Brand]:
        """Return brands enabled in one country."""
        enabled = {
            row.brand_id
            for row in self.brand_countries
            if row.country_id == country_id and row.enabled
        }
        return [
            row for row in self.brands if row.brand_id in enabled and row.status is RecordStatus.ACTIVE
        ]

    def enable_brand(self, brand_id: UUID, country_id: UUID) -> None:
        """Enable one brand in one country."""
        self._enable(self.brand_countries, BrandCountry, "brand_id", brand_id, country_id)

    def list_brand_countries(self, brand_id: UUID) -> list[BrandCountry]:
        """Return enablement rows for one brand."""
        return [row for row in self.brand_countries if row.brand_id == brand_id]

    def add_brand_request(self, request: BrandRequest) -> None:
        """Record a brand request."""
        self.brand_requests.append(request)

    def get_brand_request(self, request_id: UUID, *, for_update: bool = False) -> BrandRequest | None:
        """Return one brand request."""
        del for_update
        return next((row for row in self.brand_requests if row.request_id == request_id), None)

    def _enable(self, rows: list, model: type, id_name: str, record_id: UUID, country_id: UUID) -> None:
        """Insert or turn on one enablement row."""
        for row in rows:
            if getattr(row, id_name) == record_id and row.country_id == country_id:
                row.enabled = True
                return
        rows.append(model(**{id_name: record_id, "country_id": country_id, "enabled": True}))


def _actor(user_id: UUID) -> AuthContext:
    """Build one authenticated actor."""
    return AuthContext(user_id, uuid4(), 1, ("password",), NOW, uuid4())


def _service(repository: Repository | None = None) -> tuple[CatalogTaxonomyService, Repository, Consume]:
    """Build a taxonomy service over an in-memory repository."""
    repository = repository or Repository()
    consume = Consume()
    service = CatalogTaxonomyService(repository, Countries(), Authorizer(), Sellers(), consume)
    return service, repository, consume


def test_vendor_owner_cannot_create_a_category() -> None:
    """Reject a vendor who tries to publish a category directly."""
    service, repository, _consume = _service()
    with pytest.raises(CatalogServiceError) as captured:
        service.create_category(
            _actor(OWNER),
            code="shoes",
            parent_id=None,
            locale_code="ne",
            name="Shoes",
        )
    assert captured.value.status_code == 403
    assert repository.categories == []


def test_category_stays_hidden_until_the_country_enables_it() -> None:
    """Hide a canonical category until Nepal enables it, and leave the UAE hidden."""
    service, repository, consume = _service()
    created = service.create_category(
        _actor(ADMIN),
        code="Shoes",
        parent_id=None,
        locale_code="ne",
        name="Shoes",
    )
    assert service.list_categories("NP", "ne") == []
    assert service.list_categories("AE", "en") == []
    with pytest.raises(CatalogServiceError) as denied:
        service.enable_category(
            _actor(REVIEWER),
            created.category_id,
            country_code="AE",
            step_up_token="grant",
        )
    assert denied.value.status_code == 403
    service.enable_category(
        _actor(REVIEWER),
        created.category_id,
        country_code="NP",
        step_up_token="grant",
    )
    listed = service.list_categories("NP", "ne")
    assert [item.code for item in listed] == ["shoes"]
    assert listed[0].name == "Shoes"
    assert service.list_categories("AE", "en") == []
    assert consume.calls == [TAXONOMY_DECIDE]
    assert len(repository.category_countries) == 1
    assert repository.category_countries[0].country_id == NEPAL


def test_category_request_does_not_insert_a_category() -> None:
    """Keep a vendor proposal pending and out of the category table."""
    service, repository, _consume = _service()
    view = service.request_category(
        _actor(OWNER),
        vendor_id=VENDOR,
        country_code="NP",
        proposed_code="bags",
        proposed_parent_id=None,
        category_id=None,
    )
    assert view.status is RequestStatus.PENDING
    assert repository.categories == []
    assert repository.category_requests[0].status is RequestStatus.PENDING


def test_brand_request_stays_pending_without_a_brand_row() -> None:
    """Store a brand proposal without creating a brand."""
    service, repository, _consume = _service()
    view = service.request_brand(
        _actor(OWNER),
        vendor_id=VENDOR,
        country_code="NP",
        proposed_name="Nike",
    )
    assert view.status is RequestStatus.PENDING
    assert view.brand_id is None
    assert repository.brands == []


def test_approving_a_brand_enables_only_the_request_country() -> None:
    """Create one brand and one Nepal enablement row."""
    service, repository, _consume = _service()
    request = service.request_brand(
        _actor(OWNER),
        vendor_id=VENDOR,
        country_code="NP",
        proposed_name="Nike",
    )
    approved = service.decide_brand(
        _actor(REVIEWER),
        request.request_id,
        decision=ReviewDecision.APPROVED,
        link_brand_id=None,
        step_up_token="grant",
    )
    assert approved.status is RequestStatus.APPROVED
    assert len(repository.brands) == 1
    assert [row.country_id for row in repository.brand_countries] == [NEPAL]
    assert service.list_brands("NP")[0].name == "Nike"
    assert service.list_brands("AE") == []


def test_same_normalized_name_links_to_one_brand() -> None:
    """Require confirmation before a second spelling joins the existing brand."""
    service, repository, _consume = _service()
    first = service.request_brand(
        _actor(OWNER),
        vendor_id=VENDOR,
        country_code="NP",
        proposed_name="Nike",
    )
    approved = service.decide_brand(
        _actor(REVIEWER),
        first.request_id,
        decision=ReviewDecision.APPROVED,
        link_brand_id=None,
        step_up_token="grant",
    )
    second = service.request_brand(
        _actor(OWNER),
        vendor_id=VENDOR,
        country_code="NP",
        proposed_name="NIKE",
    )
    with pytest.raises(CatalogServiceError) as captured:
        service.decide_brand(
            _actor(REVIEWER),
            second.request_id,
            decision=ReviewDecision.APPROVED,
            link_brand_id=None,
            step_up_token="grant",
        )
    assert captured.value.code == "BRAND_LINK_REQUIRED"
    linked = service.decide_brand(
        _actor(REVIEWER),
        second.request_id,
        decision=ReviewDecision.APPROVED,
        link_brand_id=approved.brand_id,
        step_up_token="grant",
    )
    assert linked.brand_id == approved.brand_id
    assert len(repository.brands) == 1


def test_retired_brand_cannot_be_enabled() -> None:
    """Refuse to enable a retired brand even when the link is confirmed."""
    service, repository, _consume = _service()
    brand = Brand(
        brand_id=uuid4(),
        name="Nike",
        normalized_name="nike",
        status=RecordStatus.RETIRED,
    )
    repository.brands.append(brand)
    request = service.request_brand(
        _actor(OWNER),
        vendor_id=VENDOR,
        country_code="NP",
        proposed_name="Nike",
    )
    with pytest.raises(CatalogServiceError) as captured:
        service.decide_brand(
            _actor(REVIEWER),
            request.request_id,
            decision=ReviewDecision.APPROVED,
            link_brand_id=brand.brand_id,
            step_up_token="grant",
        )
    assert captured.value.code == "BRAND_RETIRED"
    assert repository.brand_countries == []
    assert repository.brand_requests[0].status is RequestStatus.PENDING


def test_request_payload_cannot_set_approved_status() -> None:
    """Reject a vendor payload that tries to approve itself."""
    with pytest.raises(ValidationError):
        BrandRequestCreate.model_validate(
            {
                "vendor_id": str(VENDOR),
                "country_code": "NP",
                "proposed_name": "Nike",
                "status": "APPROVED",
            }
        )


def test_failed_step_up_does_not_enable_a_category() -> None:
    """Leave the category disabled when the step-up grant is rejected."""
    service, repository, _consume = _service()
    created = service.create_category(
        _actor(ADMIN),
        code="shoes",
        parent_id=None,
        locale_code="ne",
        name="Shoes",
    )
    with pytest.raises(RuntimeError):
        service.enable_category(
            _actor(REVIEWER),
            created.category_id,
            country_code="NP",
            step_up_token="bad",
        )
    assert repository.category_countries == []


def test_category_code_stays_immutable() -> None:
    """Reject a proposal that would change an existing category code."""
    service, repository, _consume = _service()
    created = service.create_category(
        _actor(ADMIN),
        code="shoes",
        parent_id=None,
        locale_code="ne",
        name="Shoes",
    )
    request = service.request_category(
        _actor(OWNER),
        vendor_id=VENDOR,
        country_code="NP",
        proposed_code="boots",
        proposed_parent_id=None,
        category_id=created.category_id,
    )
    with pytest.raises(CatalogServiceError) as captured:
        service.decide_category(
            _actor(REVIEWER),
            request.request_id,
            decision=ReviewDecision.APPROVED,
            step_up_token="grant",
        )
    assert captured.value.code == "CODE_IMMUTABLE"
    assert repository.categories[0].code == "shoes"


def test_super_admin_replaces_category_attributes() -> None:
    """Replace the stored attribute set and keep vendors from writing it."""
    service, repository, _consume = _service()
    created = service.create_category(
        _actor(ADMIN),
        code="shoes",
        parent_id=None,
        locale_code="ne",
        name="Shoes",
    )
    attribute = AttributeDefinition(
        attribute_id=uuid4(),
        code="size",
        data_type=AttributeDataType.OPTION,
        status=RecordStatus.ACTIVE,
    )
    repository.attributes.append(attribute)
    with pytest.raises(CatalogServiceError) as denied:
        service.replace_attributes(
            _actor(OWNER),
            created.category_id,
            [AttributeLink(attribute.attribute_id, True)],
        )
    assert denied.value.status_code == 403
    count = service.replace_attributes(
        _actor(ADMIN),
        created.category_id,
        [AttributeLink(attribute.attribute_id, True)],
    )
    assert count == 1
    assert repository.attribute_links[0].required is True
