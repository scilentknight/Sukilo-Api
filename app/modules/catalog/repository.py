"""Persist shared taxonomy without committing the transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

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
    MediaAsset,
    OfferCountry,
    CatalogProduct,
    ProductAttributeValue,
    ProductCountry,
    ProductMedia,
    ProductTranslation,
    SellerOffer,
    Sku,
    SkuAttributeValue,
)
from app.modules.catalog.types import (
    ModerationStatus,
    OfferStatus,
    ProductStatus,
    PublicationStatus,
    RecordStatus,
)


class CatalogRepository:
    """Provide focused persistence for categories, attributes, and brands."""

    def __init__(self, db: Session) -> None:
        """Initialize the repository.

        Args:
            db: Active SQLAlchemy session.
        """
        self.db = db

    def add_category(self, category: Category) -> None:
        """Stage one category.

        Args:
            category: Category to insert.
        """
        self.db.add(category)

    def get_category(
        self,
        category_id: UUID,
        *,
        for_update: bool = False,
    ) -> Category | None:
        """Return one category.

        Args:
            category_id: Category identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching category, or `None`.
        """
        statement = select(Category).where(Category.category_id == category_id)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def find_category_by_code(self, code: str) -> Category | None:
        """Return the category that already uses a code.

        Args:
            code: Normalized category code.

        Returns:
            Matching category, or `None`.
        """
        return self.db.scalar(select(Category).where(Category.code == code))

    def add_translation(self, translation: CategoryTranslation) -> None:
        """Stage one category translation.

        Args:
            translation: Translation to insert.
        """
        self.db.add(translation)

    def get_translation(self, category_id: UUID, locale_code: str) -> CategoryTranslation | None:
        """Return one category translation.

        Args:
            category_id: Category identifier.
            locale_code: Locale code.

        Returns:
            Matching translation, or `None`.
        """
        statement = select(CategoryTranslation).where(
            CategoryTranslation.category_id == category_id,
            CategoryTranslation.locale_code == locale_code,
        )
        return self.db.scalar(statement)

    def list_enabled_categories(self, country_id: UUID) -> list[Category]:
        """Return active categories enabled in one country.

        Args:
            country_id: Market identifier.

        Returns:
            Enabled categories ordered by code.
        """
        statement = (
            select(Category)
            .join(CategoryCountry, CategoryCountry.category_id == Category.category_id)
            .where(
                CategoryCountry.country_id == country_id,
                CategoryCountry.enabled.is_(True),
                Category.status == RecordStatus.ACTIVE,
            )
            .order_by(Category.code)
        )
        return list(self.db.scalars(statement))

    def enable_category(self, category_id: UUID, country_id: UUID) -> None:
        """Enable one category in one country.

        Args:
            category_id: Category identifier.
            country_id: Market identifier.
        """
        self._enable(CategoryCountry, "category_id", category_id, country_id)

    def add_category_request(self, request: CategoryChangeRequest) -> None:
        """Stage one category change request.

        Args:
            request: Request to insert.
        """
        self.db.add(request)

    def get_category_request(
        self,
        request_id: UUID,
        *,
        for_update: bool = False,
    ) -> CategoryChangeRequest | None:
        """Return one category change request.

        Args:
            request_id: Request identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching request, or `None`.
        """
        statement = select(CategoryChangeRequest).where(
            CategoryChangeRequest.request_id == request_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_attribute(self, attribute_id: UUID) -> AttributeDefinition | None:
        """Return one attribute definition.

        Args:
            attribute_id: Attribute identifier.

        Returns:
            Matching attribute, or `None`.
        """
        statement = select(AttributeDefinition).where(
            AttributeDefinition.attribute_id == attribute_id,
        )
        return self.db.scalar(statement)

    def replace_category_attributes(
        self,
        category_id: UUID,
        links: list[tuple[UUID, bool]],
    ) -> None:
        """Replace the attribute links for one category.

        Args:
            category_id: Category identifier.
            links: Attribute identifiers and required flags.
        """
        statement = select(CategoryAttribute).where(CategoryAttribute.category_id == category_id)
        for row in self.db.scalars(statement):
            self.db.delete(row)
        self.db.flush()
        for attribute_id, required in links:
            self.db.add(
                CategoryAttribute(
                    category_id=category_id,
                    attribute_id=attribute_id,
                    required=required,
                )
            )

    def list_category_attributes(self, category_id: UUID) -> list[CategoryAttribute]:
        """Return attribute links for one category.

        Args:
            category_id: Category identifier.

        Returns:
            Attribute links.
        """
        statement = select(CategoryAttribute).where(CategoryAttribute.category_id == category_id)
        return list(self.db.scalars(statement))

    def add_brand(self, brand: Brand) -> None:
        """Stage one brand.

        Args:
            brand: Brand to insert.
        """
        self.db.add(brand)

    def get_brand(self, brand_id: UUID, *, for_update: bool = False) -> Brand | None:
        """Return one brand.

        Args:
            brand_id: Brand identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching brand, or `None`.
        """
        statement = select(Brand).where(Brand.brand_id == brand_id)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def find_brand_by_normalized_name(self, normalized_name: str) -> Brand | None:
        """Return the brand that already uses a normalized name.

        Args:
            normalized_name: Normalized brand name.

        Returns:
            Matching brand, or `None`.
        """
        statement = select(Brand).where(Brand.normalized_name == normalized_name)
        return self.db.scalar(statement)

    def list_enabled_brands(self, country_id: UUID) -> list[Brand]:
        """Return active brands enabled in one country.

        Args:
            country_id: Market identifier.

        Returns:
            Enabled brands ordered by name.
        """
        statement = (
            select(Brand)
            .join(BrandCountry, BrandCountry.brand_id == Brand.brand_id)
            .where(
                BrandCountry.country_id == country_id,
                BrandCountry.enabled.is_(True),
                Brand.status == RecordStatus.ACTIVE,
            )
            .order_by(Brand.name)
        )
        return list(self.db.scalars(statement))

    def enable_brand(self, brand_id: UUID, country_id: UUID) -> None:
        """Enable one brand in one country.

        Args:
            brand_id: Brand identifier.
            country_id: Market identifier.
        """
        self._enable(BrandCountry, "brand_id", brand_id, country_id)

    def list_brand_countries(self, brand_id: UUID) -> list[BrandCountry]:
        """Return country enablement rows for one brand.

        Args:
            brand_id: Brand identifier.

        Returns:
            Country enablement rows.
        """
        statement = select(BrandCountry).where(BrandCountry.brand_id == brand_id)
        return list(self.db.scalars(statement))

    def add_brand_request(self, request: BrandRequest) -> None:
        """Stage one brand request.

        Args:
            request: Request to insert.
        """
        self.db.add(request)

    def get_brand_request(
        self,
        request_id: UUID,
        *,
        for_update: bool = False,
    ) -> BrandRequest | None:
        """Return one brand request.

        Args:
            request_id: Request identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching request, or `None`.
        """
        statement = select(BrandRequest).where(BrandRequest.request_id == request_id)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def _enable(self, model: type, id_name: str, record_id: UUID, country_id: UUID) -> None:
        """Insert or turn on one country enablement row."""
        statement = select(model).where(
            getattr(model, id_name) == record_id,
            model.country_id == country_id,
        )
        row = self.db.scalar(statement)
        if row is None:
            self.db.add(model(**{id_name: record_id, "country_id": country_id, "enabled": True}))
            return
        row.enabled = True

    def is_category_enabled(self, category_id: UUID, country_id: UUID) -> bool:
        """Return whether an active category is enabled in one country."""
        category = self.get_category(category_id)
        if category is None or category.status is not RecordStatus.ACTIVE:
            return False
        row = self.db.scalar(
            select(CategoryCountry).where(
                CategoryCountry.category_id == category_id,
                CategoryCountry.country_id == country_id,
                CategoryCountry.enabled.is_(True),
            )
        )
        return row is not None

    def is_brand_enabled(self, brand_id: UUID, country_id: UUID) -> bool:
        """Return whether an active brand is enabled in one country."""
        brand = self.get_brand(brand_id)
        if brand is None or brand.status is not RecordStatus.ACTIVE:
            return False
        row = self.db.scalar(
            select(BrandCountry).where(
                BrandCountry.brand_id == brand_id,
                BrandCountry.country_id == country_id,
                BrandCountry.enabled.is_(True),
            )
        )
        return row is not None

    def list_required_attribute_ids(self, category_id: UUID) -> list[UUID]:
        """Return attribute ids required by one category."""
        statement = select(CategoryAttribute.attribute_id).where(
            CategoryAttribute.category_id == category_id,
            CategoryAttribute.required.is_(True),
        )
        return list(self.db.scalars(statement))

    def list_product_attribute_ids(self, product_id: UUID) -> list[UUID]:
        """Return attribute ids already stored on one product."""
        statement = select(ProductAttributeValue.attribute_id).where(
            ProductAttributeValue.product_id == product_id
        )
        return list(self.db.scalars(statement))

    def add_product(self, product: CatalogProduct) -> None:
        """Stage one shared product."""
        self.db.add(product)
        self.db.flush()

    def add_product_translation(self, translation: ProductTranslation) -> None:
        """Stage one product translation."""
        self.db.add(translation)

    def add_product_attribute(self, value: ProductAttributeValue) -> None:
        """Stage one product attribute value."""
        self.db.add(value)

    def get_product(self, product_id: UUID, *, for_update: bool = False) -> CatalogProduct | None:
        """Return one shared product."""
        statement = select(CatalogProduct).where(CatalogProduct.product_id == product_id)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_product_country(
        self,
        product_id: UUID,
        country_id: UUID,
        *,
        for_update: bool = False,
    ) -> ProductCountry | None:
        """Return moderation for one product in one country."""
        statement = select(ProductCountry).where(
            ProductCountry.product_id == product_id,
            ProductCountry.country_id == country_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def set_product_moderation(
        self,
        product_id: UUID,
        country_id: UUID,
        status: ModerationStatus,
    ) -> None:
        """Insert or update moderation for one country only."""
        row = self.get_product_country(product_id, country_id)
        if row is None:
            self.db.add(
                ProductCountry(
                    product_id=product_id,
                    country_id=country_id,
                    moderation_status=status,
                )
            )
            return
        row.moderation_status = status

    def add_sku(self, sku: Sku) -> None:
        """Stage one SKU."""
        self.db.add(sku)
        self.db.flush()

    def add_sku_attribute(self, value: SkuAttributeValue) -> None:
        """Stage one SKU attribute value."""
        self.db.add(value)

    def get_sku(self, sku_id: UUID) -> Sku | None:
        """Return one SKU."""
        return self.db.scalar(select(Sku).where(Sku.sku_id == sku_id))

    def add_media(self, media: MediaAsset) -> None:
        """Stage one media asset."""
        self.db.add(media)
        self.db.flush()

    def clear_primary_media(self, product_id: UUID) -> None:
        """Clear the primary flag on every media row for one product."""
        rows = self.db.scalars(
            select(ProductMedia).where(ProductMedia.product_id == product_id)
        )
        for row in rows:
            row.is_primary = False

    def add_product_media(self, link: ProductMedia) -> None:
        """Stage one product media link."""
        self.db.add(link)

    def add_offer(self, offer: SellerOffer) -> None:
        """Stage one seller offer."""
        self.db.add(offer)
        self.db.flush()

    def add_offer_country(self, row: OfferCountry) -> None:
        """Stage one offer country price."""
        self.db.add(row)

    def get_offer(self, offer_id: UUID, *, for_update: bool = False) -> SellerOffer | None:
        """Return one seller offer."""
        statement = select(SellerOffer).where(SellerOffer.offer_id == offer_id)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_offer_country(
        self,
        offer_id: UUID,
        country_id: UUID,
        *,
        for_update: bool = False,
    ) -> OfferCountry | None:
        """Return one offer publication row."""
        statement = select(OfferCountry).where(
            OfferCountry.offer_id == offer_id,
            OfferCountry.country_id == country_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_product_name(self, product_id: UUID, locale_code: str) -> str | None:
        """Return one product translation name."""
        row = self.db.scalar(
            select(ProductTranslation).where(
                ProductTranslation.product_id == product_id,
                ProductTranslation.locale_code == locale_code,
            )
        )
        if row is None:
            return None
        return row.name

    def list_published_offers(
        self,
        country_id: UUID,
    ) -> list[tuple[SellerOffer, OfferCountry, Sku, CatalogProduct, ProductCountry]]:
        """Return published offer rows that still pass country publication checks."""
        statement = (
            select(SellerOffer, OfferCountry, Sku, CatalogProduct, ProductCountry)
            .join(OfferCountry, OfferCountry.offer_id == SellerOffer.offer_id)
            .join(Sku, Sku.sku_id == SellerOffer.sku_id)
            .join(CatalogProduct, CatalogProduct.product_id == Sku.product_id)
            .join(
                ProductCountry,
                (ProductCountry.product_id == CatalogProduct.product_id)
                & (ProductCountry.country_id == country_id),
            )
            .where(
                OfferCountry.country_id == country_id,
                OfferCountry.publication_status == PublicationStatus.PUBLISHED,
                SellerOffer.status == OfferStatus.ACTIVE,
                ProductCountry.moderation_status == ModerationStatus.APPROVED,
                CatalogProduct.status != ProductStatus.RETIRED,
            )
        )
        return list(self.db.execute(statement).all())
