# Phase 4 — Taxonomy, Attributes, and Brands

## Goal

Create the shared catalog language: categories, attributes, and brands, plus
the country switch that enables each one in a market.

Vendors do not invent root categories or merge brands. They may request a
brand and propose a category change. Country operations enables records for
one country. Super admin owns the canonical tree.

This phase does not create products, SKUs, media, or seller offers.

## Prerequisites

- Phase 3 store activation is tested. Catalog rows do not depend on a store,
  but the seller request routes require an approved vendor country.
- `CountryReader` resolves country and locale.
- `SUPER_ADMIN` still receives every permission added to the access catalog.
- `public.products.category` and `public.products.brand` stay untouched.

## Deliverables

- PostgreSQL schema `catalog`.
- Category, category-country, category translation, and category change request.
- Attribute definition, option, translations, and category-attribute link.
- Brand, brand-country, and brand request.
- Read routes for enabled taxonomy.
- Request routes for an approved vendor.
- Enable and approve routes for country operations and super admin.
- Tests that a vendor cannot publish a category by inserting it directly.

## Planned file ownership

Create:

```text
app/modules/catalog/__init__.py
app/modules/catalog/models/__init__.py
app/modules/catalog/models/category.py
app/modules/catalog/models/attribute.py
app/modules/catalog/models/brand.py
app/modules/catalog/repository.py
app/modules/catalog/service.py
app/modules/catalog/schemas.py
app/modules/catalog/types.py
app/api/v1/endpoints/catalog_taxonomy.py
tests/unit/catalog/test_taxonomy_service.py
tests/integration/catalog/test_taxonomy_repository.py
alembic/versions/20261028_0031_create_catalog_schema.py
alembic/versions/20261028_0032_create_catalog_category.py
alembic/versions/20261028_0033_create_catalog_attribute.py
alembic/versions/20261028_0034_create_catalog_brand.py
alembic/versions/20261028_0035_seed_catalog_permissions.py
```

Modify:

```text
app/models/__init__.py
app/api/v1/router.py
app/modules/access/catalog.py
```

## Database design

### Categories

`catalog.category` is one shared node.

| Column | Rule |
| --- | --- |
| `category_id` | UUID primary key. |
| `parent_id` | Nullable self foreign key. Null is a root. |
| `code` | Unique stable code. |
| `status` | `ACTIVE` or `RETIRED`. |
| `created_at`, `updated_at` | UTC. |

`catalog.category_country` primary key is `(category_id, country_id)`.
`enabled` defaults to false. A retired category cannot be enabled.

`catalog.category_translation` primary key is `(category_id, locale_code)`.
`name` is required. The category id does not change when the label changes.

`catalog.category_change_request` stores a vendor or reviewer proposal:
`request_id`, `category_id` nullable for a new node, `proposed_parent_id`,
`proposed_code`, `country_id`, `requested_by`, `status`, and `reviewed_by`.
Status is `PENDING`, `APPROVED`, or `REJECTED`. Approval of a new node inserts
the category. It does not enable it in every country.

### Attributes

`catalog.attribute_definition` has `attribute_id`, unique `code`, `data_type`
of `TEXT`, `NUMBER`, or `OPTION`, and `status`.

`catalog.attribute_option` belongs to one attribute. `code` is unique per
attribute.

`catalog.attribute_translation` and `catalog.attribute_option_translation`
use `(id, locale_code)` primary keys and a required `label`.

`catalog.category_attribute` primary key is `(category_id, attribute_id)`.
`required` is boolean. A product phase will read this table. This phase only
stores it.

### Brands

`catalog.brand` has `brand_id`, unique `name`, optional `normalized_name`,
and `status` of `ACTIVE` or `RETIRED`. `normalized_name` is unique so two
spellings of one brand cannot both become canonical.

`catalog.brand_country` primary key is `(brand_id, country_id)` with
`enabled` defaulting to false.

`catalog.brand_request` has `request_id`, `proposed_name`, `country_id`,
`vendor_id`, `requested_by`, `status`, `reviewed_by`, and optional
`brand_id` set when an existing brand is linked instead of created.

## Permissions

Seed:

- `catalog.category.write` for `SUPER_ADMIN` only.
- `catalog.category.enable` for `COUNTRY_OPERATIONS` and `SUPER_ADMIN`.
- `catalog.brand.review` for `COUNTRY_OPERATIONS` and `SUPER_ADMIN`.
- `catalog.taxonomy.request` for `VENDOR_OWNER`.

Do not give vendors `catalog.category.write`.

## Routes

| Method and path | Caller | Result |
| --- | --- | --- |
| `GET /api/v1/catalog/categories` | Public | Enabled categories for `country_code`, with the requested locale. |
| `POST /api/v1/catalog/categories` | Super admin | Creates a canonical category. It is disabled in every country. |
| `POST /api/v1/catalog/categories/{id}/enable` | Country operations | Enables that category in the reviewer's country. |
| `POST /api/v1/catalog/category-requests` | Vendor owner | Proposes a category change for one country. |
| `POST /api/v1/catalog/category-requests/{id}/decide` | Country operations or super admin | Approves or rejects. |
| `GET /api/v1/catalog/brands` | Public | Enabled brands for the country. |
| `POST /api/v1/catalog/brand-requests` | Vendor owner | Requests a brand. |
| `POST /api/v1/catalog/brand-requests/{id}/decide` | Country reviewer | Links an existing brand or creates one, then enables it only in that country. |
| `PUT /api/v1/catalog/categories/{id}/attributes` | Super admin | Replaces the required attribute set. |

Enable and decide routes require `X-Step-Up-Grant` with action class
`catalog.taxonomy.decide`.

## Service rules

- A vendor request does not insert into `catalog.category` or `catalog.brand`.
- Enabling a category in Nepal does not enable it in the UAE.
- A retired category or brand cannot be enabled.
- Approving a brand request prefers an existing `normalized_name` match. The
  reviewer must confirm the link. The service does not silently merge two
  different brands.
- Public list routes return only `enabled` rows for the resolved country and
  only `ACTIVE` parents.
- Category codes are immutable after insert.

## Required tests

- A vendor owner cannot call category create.
- A category created by super admin is hidden from the public list until a
  country enables it.
- Nepal enablement does not appear in a UAE list.
- A brand request stays `PENDING` and creates no brand row.
- Approving a brand request creates one brand and one country enablement row.
- Two requests with the same normalized name can be linked to one brand.
- A retired brand cannot be enabled.
- `public.products` is unchanged.

## Security checks

- Decide routes require step-up.
- A country reviewer cannot enable a category for a country outside the
  assignment.
- Request payloads cannot set `status` to `APPROVED`.

## Completion criteria

Phase 4 is complete when:

- Canonical categories and brands exist only through the super-admin or review
  decision paths.
- Country enablement is per country.
- Vendors can request changes and cannot apply them themselves.
- No product, SKU, media, or offer table exists yet.

## Handoff to Phase 5

Phase 5 reads enabled categories, required attributes, and enabled brands. It
must refuse a product in a category that is disabled for the target country,
and it must refuse a brand that is not enabled there.
