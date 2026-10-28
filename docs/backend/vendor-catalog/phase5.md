# Phase 5 — Product, Media, and Seller Offer

## Goal

Let an approved seller publish a commercial offer for one SKU from one store
in one country, using the shared product catalog.

Two sellers of the same SKU create two `catalog.seller_offer` rows. They do
not create two products. This phase does not add stock counts, reservations,
carts, checkout, or payments. It does not write `public.products`.

## Prerequisites

- Phase 3 can mark a vendor country `selling_enabled`.
- Phase 4 can answer whether a category and brand are enabled in a country,
  and which attributes that category requires.
- `VendorScopeReader` reports selling enablement and store ownership.
- Document and store media columns that were left without a foreign key can
  now reference `catalog.media_asset`.

## Deliverables

- Product, product-country, translation, and product attribute values.
- SKU and SKU attribute values.
- Media asset and product media order.
- Seller offer and offer-country publication.
- Foreign keys from `vendor.vendor_document.storage_key` remain. Add nullable
  `media_id` on documents and store logo or banner columns.
- Seller routes to create a product draft, attach media, and publish an offer.
- Country moderation before a product is publicly visible.
- Tests that a second vendor reuses one SKU.

## Planned file ownership

Create:

```text
app/modules/catalog/models/product.py
app/modules/catalog/models/sku.py
app/modules/catalog/models/media.py
app/modules/catalog/models/offer.py
app/modules/catalog/offers.py
app/ports/vendor_scope.py
app/api/v1/endpoints/catalog_offers.py
tests/unit/catalog/test_offer_service.py
tests/integration/catalog/test_offer_repository.py
tests/security/test_offer_country_scope.py
alembic/versions/20261028_0036_create_catalog_product.py
alembic/versions/20261028_0037_create_catalog_sku.py
alembic/versions/20261028_0038_create_catalog_media.py
alembic/versions/20261028_0039_create_catalog_offer.py
alembic/versions/20261028_0040_link_vendor_media.py
alembic/versions/20261028_0041_seed_offer_permissions.py
```

Modify:

```text
app/models/__init__.py
app/api/v1/router.py
app/modules/access/catalog.py
app/modules/catalog/repository.py
app/modules/vendor/models/vendor_document.py
app/modules/vendor/models/store.py
```

## Database design

### Product

`catalog.product` holds shared identity: `product_id`, `brand_id`,
`category_id`, `status` of `DRAFT`, `ACTIVE`, or `RETIRED`, `created_by`,
and timestamps. It has no price and no vendor id.

`catalog.product_country` primary key is `(product_id, country_id)`.
`moderation_status` is `PENDING`, `APPROVED`, or `REJECTED`. Public reads
require `APPROVED`.

`catalog.product_translation` primary key is `(product_id, locale_code)`
with required `name` and optional `description`.

`catalog.product_attribute_value` stores one value per product and attribute.
Option attributes store `option_id`. Text and number attributes store
`value_text` or `value_number`. Exactly one of those three is set.

### SKU

`catalog.sku` belongs to one product. `sku_code` is unique per product.
`catalog.sku_attribute_value` uses the same value rule as product attributes
and identifies the variant, such as size.

### Media

`catalog.media_asset` stores `media_id`, opaque `storage_key`, `media_type`,
`created_by`, and timestamps. Bytes stay in the document storage port.

`catalog.product_media` primary key is `(product_id, media_id)` with
`sort_order`. One row per product may be `is_primary`.

New nullable foreign keys:

- `vendor.vendor_document.media_id`
- `vendor.store.logo_media_id`
- `vendor.store.banner_media_id`

Existing `storage_key` values remain valid.

### Offer

`catalog.seller_offer` is one vendor selling one SKU in one store.

| Column | Rule |
| --- | --- |
| `offer_id` | UUID primary key. |
| `vendor_id` | The seller organization. |
| `store_id` | Store in the same vendor. |
| `sku_id` | Shared SKU. |
| `seller_sku` | Required seller code, unique per vendor. |
| `status` | `DRAFT`, `ACTIVE`, or `SUSPENDED`. |
| `version` | Optimistic integer starting at 1. |
| `created_at`, `updated_at` | UTC. |

Unique `(vendor_id, sku_id, store_id)` so one store cannot list the same SKU
twice. Another vendor may still offer that SKU.

`catalog.offer_country` primary key is `(offer_id, country_id)`.
`publication_status` is `DRAFT` or `PUBLISHED`. `currency_code` is the country
currency. `list_price` is a positive decimal. This table does not store
available quantity.

## Permissions

Seed:

- `catalog.product.write` for `VENDOR_OWNER` and `SUPER_ADMIN`.
- `catalog.product.moderate` for `COUNTRY_OPERATIONS` and `SUPER_ADMIN`.
- `catalog.offer.publish` for `VENDOR_OWNER`.

`VENDOR_STAFF` does not receive publish permission in this phase. A later
grant can add it per vendor. Do not give staff publish by default.

## Routes

| Method and path | Caller | Result |
| --- | --- | --- |
| `POST /api/v1/catalog/products` | Vendor owner | Draft shared product in an enabled category. |
| `POST /api/v1/catalog/products/{id}/skus` | Vendor owner | Adds a variant. |
| `POST /api/v1/catalog/products/{id}/media` | Vendor owner | Attaches media metadata. |
| `POST /api/v1/catalog/products/{id}/submit` | Vendor owner | Sets each target country row to `PENDING`. |
| `POST /api/v1/catalog/products/{id}/moderate` | Country operations | Approves or rejects that country only. |
| `POST /api/v1/catalog/offers` | Vendor owner | Draft offer for an approved SKU, owned store, and selling-enabled country. |
| `POST /api/v1/catalog/offers/{id}/publish` | Vendor owner | Sets `offer_country` to `PUBLISHED` when the product is approved there. |
| `GET /api/v1/catalog/offers` | Public | Published offers for one country. No draft rows. |

Moderate and publish require `X-Step-Up-Grant`. Moderate uses
`catalog.product.moderate`. Publish uses `catalog.offer.publish`.

## Service rules

- The category and brand must be enabled in the product's target country.
- Required category attributes must have values before submit.
- A vendor cannot set `moderation_status` to `APPROVED`.
- Approving a product in Nepal does not approve it in the UAE.
- The offer store must belong to `vendor_id` and `country_id`.
- `VendorScopeReader.is_selling_enabled` must be true before publish.
- Publish locks the offer and the offer-country row.
- A published offer has a price and currency. It does not have a stock count.
- Public reads hide drafts, rejected products, and suspended offers.
- Creating a product does not insert into `public.products`.

## Required tests

- Two vendors offering one SKU leave one product row and two offer rows.
- A seller cannot publish into a country where the category is disabled.
- A seller cannot publish while `selling_enabled` is false.
- Nepal moderation does not make the product public in the UAE.
- A staff member without `catalog.offer.publish` receives `403`.
- Publish is rejected when the product country is still `PENDING`.
- Offer responses do not include a quantity field.
- `public.products` row count is unchanged.
- Store logo updates can reference a real `media_id`.

## Security checks

- Price is server-validated as a positive decimal. The client does not send a
  trusted total.
- Media routes return `storage_key` only to the uploader's vendor, never on the
  public offer list.
- A guessed offer id from another vendor returns `404` on the owner update
  route.
- Step-up is required to publish.

## Completion criteria

Phase 5 is complete when:

- A selling-enabled owner can submit a product and publish one offer in one
  approved country.
- Another owner can offer the same SKU without a second product.
- Country moderation and publication do not cross markets.
- No inventory ledger, cart, or payment table was created.
- The temporary public product tables were not used as the seller catalog.

## Handoff

The inventory module may add warehouse quantity and reservation against
`catalog.seller_offer`. The commerce module may add a cart line that references
`offer_id` and `country_id`. Neither module may copy product rows per seller,
and neither may treat a draft offer as buyable.
