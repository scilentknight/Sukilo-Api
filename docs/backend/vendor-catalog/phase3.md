# Phase 3 — Payout, Store, and Warehouse

## Goal

Let an approved vendor owner configure the country shop: a payout destination,
a storefront, localized store copy, and at least one warehouse.

The store becomes `ACTIVE`, and `selling_enabled` becomes true, only when those
pieces pass the go-live checks. This phase does not create products, offers,
stock counts, or payout transfers.

## Prerequisites

- Phase 2 approval can create `VENDOR_OWNER` for one vendor and country.
- `selling_enabled` and `payout_enabled` are still false after approval.
- `CountryReader` can validate a currency and a locale for that country.
- Customer addresses remain shopper addresses. A warehouse must not point at
  another user's `iam.address` row.

## Deliverables

- `vendor.vendor_payout_account`.
- `vendor.store` and `vendor.store_translation`.
- `vendor.warehouse` with its own address fields.
- Store activation that flips `selling_enabled` only after the checks pass.
- `STORE_STAFF` is not seeded. A store id exists, but staff assignment at store
  scope waits until a later assignment port can prove store ancestry. This
  phase seeds `vendor.store.write` and `vendor.warehouse.write` on
  `VENDOR_OWNER`.
- Routes for the owner of that vendor and country.

## Planned file ownership

Create:

```text
app/modules/vendor/models/payout_account.py
app/modules/vendor/models/store.py
app/modules/vendor/models/store_translation.py
app/modules/vendor/models/warehouse.py
app/modules/vendor/storefront.py
app/api/v1/endpoints/vendor_store.py
tests/unit/vendor/test_storefront_service.py
tests/integration/vendor/test_store_activation.py
alembic/versions/20261028_0026_create_vendor_payout_account.py
alembic/versions/20261028_0027_create_vendor_store.py
alembic/versions/20261028_0028_create_vendor_store_translation.py
alembic/versions/20261028_0029_create_vendor_warehouse.py
alembic/versions/20261028_0030_seed_store_permissions.py
```

Modify:

```text
app/models/__init__.py
app/api/v1/router.py
app/modules/access/catalog.py
app/modules/vendor/repository.py
app/modules/vendor/types.py
```

## Database design

### `vendor.vendor_payout_account`

Follow `architecture.md`. `account_number_encrypted` is required. API schemas
must not include it. `account_number_masked` is the only account number a
response may return. `currency_code` is three letters. `verification_status`
starts at `PENDING`.

One default payout row per vendor country and currency:

```text
uq_vendor_payout_default (vendor_country_id, currency_code)
  WHERE is_default
```

Use a partial unique index so non-default rows can coexist.

### `vendor.store`

One store per vendor and country in this phase:

```text
uq_vendor_store_vendor_country (vendor_id, country_id)
uq_vendor_store_country_slug (country_id, slug)
```

Status is `DRAFT`, `ACTIVE`, or `SUSPENDED`. `rating`, `rating_count`, and
`followers_count` default to zero and are not in the update schema.
`logo_media_id` and `banner_media_id` stay nullable and have no foreign key
until Phase 5.

### `vendor.store_translation`

Primary key `(store_id, locale_code)`. `name` is required. `description`,
`seo_title`, and `seo_description` are optional. Locale must be enabled for
the store country.

### `vendor.warehouse`

`code` is unique per vendor. Address columns live on this table in this phase:

- `address_line_1`
- `city_text`
- `postal_code`
- `contact_name`
- `contact_phone`

Do not add `address_id` until a vendor-owned address record exists. Do not
copy a shopper address id into the warehouse.

`fulfillment_enabled` and `pickup_enabled` default to false. `status` is
`DRAFT`, `ACTIVE`, or `SUSPENDED`.

## Routes

The caller must be an active `VENDOR_OWNER` for the store's vendor and the
store's country.

| Method and path | Result |
| --- | --- |
| `PUT /api/v1/vendor/stores` | Creates or updates the draft store and one translation. |
| `PUT /api/v1/vendor/payout-accounts` | Adds a payout destination. Response is masked. |
| `POST /api/v1/vendor/warehouses` | Adds a warehouse in the owner's country. |
| `POST /api/v1/vendor/stores/activate` | Runs the go-live checks and may set the store active. |

Country code is an input to `CountryReader`. A Nepal owner who sends `AE`
receives `403`.

Payout verification in this phase is an internal reviewer action:

`POST /api/v1/vendor/payout-accounts/{id}/verify`

The reviewer needs `vendor.payout.verify` in that country. Map that permission
to `COUNTRY_OPERATIONS` and `SUPER_ADMIN` only.

## Go-live checks

`activate` locks the store, the vendor country, the default payout row, and
the warehouses for that country.

All of the following must be true:

- `vendor_country.onboarding_status` is `APPROVED`.
- A default payout account for the country currency is `VERIFIED`.
- At least one warehouse is `ACTIVE` and `fulfillment_enabled`.
- The country's default locale has a store translation with a name.
- Store `slug` is unique in that country.

On success, set store status to `ACTIVE`, `payout_enabled` to true, and
`selling_enabled` to true. On failure, change nothing and return `409` with a
stable code naming the failed check. Do not publish products. There are none
yet.

## Service rules

- Creating a store before approval is rejected.
- Slug is normalized to lowercase ASCII. Spaces become hyphens. Empty slugs
  are rejected.
- Replacing the default payout clears the previous default in the same
  transaction.
- Warehouse `code` is unique per vendor, including warehouses in another
  country of the same vendor.
- Rating fields are ignored if a client sends them.
- Activation does not grant a new role. The owner badge already exists.

## Required tests

- A draft application cannot create a store.
- An approved Nepal owner cannot create a UAE warehouse.
- Payout responses contain `account_number_masked` and not the encrypted value.
- Activation fails when payout is still `PENDING`.
- Activation fails when no fulfillment warehouse is active.
- Activation sets `selling_enabled` only when every check passes.
- A failed activation leaves `selling_enabled` false.
- Two stores in one country cannot share a slug.
- Logs and audit events do not contain `account_number_encrypted`.

## Security checks

- Model `repr` omits the encrypted account number.
- Store update schemas use `extra="forbid"`.
- The owner cannot verify their own payout account.

## Completion criteria

Phase 3 is complete when:

- An approved owner can save payout, store copy, and a warehouse.
- The store stays draft until the go-live checks pass.
- A passed activation enables selling for that country only.
- No catalog table or `public.products` row was written.

## Handoff to Phase 4

Phase 4 consumes a vendor that may already be `selling_enabled` and adds the
shared category, attribute, and brand records sellers will use when they
create offers. Store activation must not be weakened to skip payout or
warehouse checks.
