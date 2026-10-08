"""Database access for products and product images."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.product import Product, ProductImage
from app.utils.gs1 import assign_product_gtin


class ProductRepository:
    """CRUD helpers for catalog tables."""

    def __init__(self, db: Session) -> None:
        """Attach an active SQLAlchemy session.

        Args:
            db: Request-scoped database session.
        """
        self._db = db

    def create_product(
        self,
        name: str,
        description: str | None,
        category: str,
        brand: str | None,
        price: Decimal,
        currency: str,
        source: str,
        gtin: str | None = None,
    ) -> Product:
        """Insert a product row and return the flushed instance.

        Args:
            name: Product display name.
            description: Optional long description.
            category: Product category label.
            brand: Optional brand name.
            price: Decimal price amount.
            currency: ISO currency code.
            source: Origin label such as vendor or kaggle.
            gtin: Optional vendor UPC/EAN/GTIN; demo GTIN used when omitted.

        Returns:
            Persisted product with database-generated primary key.
        """
        product = Product(
            name=name,
            description=description,
            category=category,
            brand=brand,
            price=price,
            currency=currency.upper(),
            source=source,
        )
        self._db.add(product)
        self._db.flush()
        product.gtin = assign_product_gtin(gtin, product.id)
        self._db.flush()
        return product

    def add_product_image(
        self,
        product_id: int,
        image_url: str,
        is_primary: bool,
    ) -> ProductImage:
        """Insert a product image row.

        Args:
            product_id: Parent product identifier.
            image_url: Public URL for the stored file.
            is_primary: Whether this image is the catalog thumbnail.

        Returns:
            Persisted product image with generated identifier.
        """
        product_image = ProductImage(
            product_id=product_id,
            image_url=image_url,
            is_primary=is_primary,
        )
        self._db.add(product_image)
        self._db.flush()
        return product_image

    def get_product(self, product_id: int) -> Product | None:
        """Load one product with related images.

        Args:
            product_id: Product primary key.

        Returns:
            Product instance or None when not found.
        """
        statement = (
            select(Product)
            .options(selectinload(Product.images))
            .where(Product.id == product_id)
        )
        return self._db.scalar(statement)

    def get_product_by_gtin(self, gtin: str) -> Product | None:
        """Load one product by normalized GTIN-14.

        Args:
            gtin: Fourteen-digit GTIN.

        Returns:
            Product instance or None when not found.
        """
        statement = (
            select(Product)
            .options(selectinload(Product.images))
            .where(Product.gtin == gtin)
        )
        return self._db.scalar(statement)

    def get_products_by_ids(self, product_ids: list[int]) -> list[Product]:
        """Load products by id preserving the caller's order.

        Args:
            product_ids: Primary keys to fetch.

        Returns:
            Products with images eager-loaded, omitting unknown ids.
        """
        if not product_ids:
            return []
        statement = (
            select(Product)
            .options(selectinload(Product.images))
            .where(Product.id.in_(product_ids))
        )
        rows = list(self._db.scalars(statement).all())
        by_id = {product.id: product for product in rows}
        return [by_id[product_id] for product_id in product_ids if product_id in by_id]

    def list_products(
        self,
        skip: int,
        limit: int,
        category: str | None = None,
        search: str | None = None,
    ) -> list[Product]:
        """Return a filtered page of products with images eager-loaded.

        Args:
            skip: Number of rows to skip.
            limit: Maximum rows to return.
            category: Optional exact category filter.
            search: Optional case-insensitive name, brand, or category query.

        Returns:
            Ordered list of products.
        """
        statement = select(Product).options(selectinload(Product.images))
        if category:
            statement = statement.where(Product.category == category)
        if search:
            term = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    Product.name.ilike(term),
                    Product.brand.ilike(term),
                    Product.category.ilike(term),
                )
            )
        statement = (
            statement.order_by(Product.id.desc()).offset(skip).limit(limit)
        )
        return list(self._db.scalars(statement).all())

    def list_categories(self) -> list[tuple[str, int]]:
        """Return distinct categories with product counts.

        Returns:
            Category labels ordered by popularity descending.
        """
        statement = (
            select(Product.category, func.count(Product.id))
            .group_by(Product.category)
            .order_by(func.count(Product.id).desc(), Product.category.asc())
        )
        rows = self._db.execute(statement).all()
        return [(str(category), int(count)) for category, count in rows]

    def count_products(
        self,
        category: str | None = None,
        search: str | None = None,
    ) -> int:
        """Count products matching catalog filters.

        Args:
            category: Optional exact category filter.
            search: Optional name, brand, or category query.

        Returns:
            Number of matching products.
        """
        statement = select(func.count(Product.id))
        if category:
            statement = statement.where(Product.category == category)
        if search:
            term = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    Product.name.ilike(term),
                    Product.brand.ilike(term),
                    Product.category.ilike(term),
                )
            )
        return int(self._db.scalar(statement) or 0)
