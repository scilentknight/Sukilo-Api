"""SQLAlchemy models for Alembic autogenerate and application imports."""

from app.modules.access.models import (
    Permission,
    Role,
    RolePermission,
    UserRoleAssignment,
)
from app.modules.customers.models import (
    Address,
    ConsentRecord,
    DataExportRequest,
    UserPreference,
)
from app.modules.identity.models import (
    AuthGrant,
    AuthIdentity,
    OtpChallenge,
    UserAccount,
    UserSession,
)
from app.models.product import Product, ProductImage

__all__ = [
    "Address",
    "AuthGrant",
    "AuthIdentity",
    "ConsentRecord",
    "DataExportRequest",
    "OtpChallenge",
    "Permission",
    "Product",
    "ProductImage",
    "Role",
    "RolePermission",
    "UserAccount",
    "UserPreference",
    "UserRoleAssignment",
    "UserSession",
]
