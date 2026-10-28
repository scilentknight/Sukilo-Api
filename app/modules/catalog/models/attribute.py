"""Define shared attribute tables."""

from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.catalog.types import AttributeDataType, RecordStatus

_DATA_TYPES = ", ".join(f"'{member.value}'" for member in AttributeDataType)
_RECORD_STATUSES = ", ".join(f"'{member.value}'" for member in RecordStatus)


class AttributeDefinition(Base):
    """Store one shared attribute definition."""

    __tablename__ = "attribute_definition"
    __table_args__ = (
        UniqueConstraint("code", name="uq_catalog_attribute_code"),
        CheckConstraint(
            f"data_type IN ({_DATA_TYPES})",
            name="ck_catalog_attribute_data_type",
        ),
        CheckConstraint(
            f"status IN ({_RECORD_STATUSES})",
            name="ck_catalog_attribute_status",
        ),
        {"schema": "catalog"},
    )

    attribute_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    data_type: Mapped[AttributeDataType] = mapped_column(
        Enum(
            AttributeDataType,
            name="catalog_attribute_data_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=16,
        ),
        nullable=False,
    )
    status: Mapped[RecordStatus] = mapped_column(
        Enum(
            RecordStatus,
            name="catalog_attribute_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=24,
        ),
        nullable=False,
        default=RecordStatus.ACTIVE,
        server_default=RecordStatus.ACTIVE.value,
    )


class AttributeOption(Base):
    """Store one allowed value for an option attribute."""

    __tablename__ = "attribute_option"
    __table_args__ = (
        UniqueConstraint(
            "attribute_id",
            "code",
            name="uq_catalog_attribute_option_code",
        ),
        {"schema": "catalog"},
    )

    option_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    attribute_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.attribute_definition.attribute_id", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)


class AttributeTranslation(Base):
    """Store one locale label for an attribute."""

    __tablename__ = "attribute_translation"
    __table_args__ = {"schema": "catalog"}

    attribute_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.attribute_definition.attribute_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    locale_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    label: Mapped[str] = mapped_column(String(160), nullable=False)


class AttributeOptionTranslation(Base):
    """Store one locale label for an attribute option."""

    __tablename__ = "attribute_option_translation"
    __table_args__ = {"schema": "catalog"}

    option_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.attribute_option.option_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    locale_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    label: Mapped[str] = mapped_column(String(160), nullable=False)


class CategoryAttribute(Base):
    """Store whether one attribute is required for one category."""

    __tablename__ = "category_attribute"
    __table_args__ = {"schema": "catalog"}

    category_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.category.category_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    attribute_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.attribute_definition.attribute_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    required: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
