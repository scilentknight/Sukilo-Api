# Phase 1 — Vendor Foundation and Organization Records

## Goal

Create the `vendor` database foundation, the seller organization records, and
the package boundary required before seller applications can be submitted.

This phase changes backend structure and persistence only. It does not expose
Seller Center routes, country review, staff invitation, store setup, or
catalog APIs. It does not seed `VENDOR_OWNER` or `VENDOR_STAFF`.

## Prerequisites

- The identity, access, and customer migrations through `20261012_0018` are
  applied.
- `iam.user_account` exists and remains the only human account.
- `docs/backend/vendor-catalog/architecture.md` is accepted as the governing
  design.
- Python 3.12 and the repository versions of FastAPI, SQLAlchemy, Pydantic, and
  Alembic remain the baseline.

## Deliverables

- PostgreSQL schema `vendor`.
- SQLAlchemy models for `vendor.vendor`, `vendor.vendor_country`, and
  `vendor.vendor_member`.
- A vendor repository that does not commit.
- Internal service methods that can create a pending organization and one
  draft country row for tests and later phases.
- Separate reversible Alembic revisions for the schema and each table.
- Unit and PostgreSQL integration tests for the phase invariants.

## Planned file ownership

Create:

```text
app/modules/vendor/__init__.py
app/modules/vendor/models/__init__.py
app/modules/vendor/models/vendor.py
app/modules/vendor/models/vendor_country.py
app/modules/vendor/models/vendor_member.py
app/modules/vendor/repository.py
app/modules/vendor/service.py
app/modules/vendor/types.py
tests/unit/vendor/test_vendor_models.py
tests/unit/vendor/test_vendor_service.py
tests/integration/vendor/test_vendor_repository.py
alembic/versions/20261028_0019_create_vendor_schema.py
alembic/versions/20261028_0020_create_vendor_vendor.py
alembic/versions/20261028_0021_create_vendor_country.py
alembic/versions/20261028_0022_create_vendor_member.py
```

Modify:

```text
app/models/__init__.py
```

`app/models/__init__.py` imports the new metadata so Alembic sees the tables.
It does not take ownership of the models. Do not modify `app/api/v1/router.py`.
Do not modify `app/modules/access/catalog.py` in this phase.

## Database design

### Migration order

1. Revision `0019` creates PostgreSQL schema `vendor`.
2. Revision `0020` creates `vendor.vendor` and its indexes.
3. Revision `0021` creates `vendor.vendor_country` and its unique country key.
4. Revision `0022` creates `vendor.vendor_member` and its unique member key.
5. Seed no roles, permissions, documents, stores, or warehouses.

Downgrades drop member, country, organization, then the empty `vendor` schema.
They do not drop `iam` tables.

### `vendor.vendor`

Implement the columns in `architecture.md`:

- UUID `vendor_id` generated in the application.
- Required `legal_name` and `display_name`.
- Constrained `vendor_type` and `status`.
- `registration_country_id` as a UUID with no foreign key to a missing market
  table.
- Optional registration number, tax number, and website URL.
- `created_by_user_id` foreign key to `iam.user_account` with
  `ON DELETE RESTRICT`.
- UTC `created_at` and `updated_at`.
- Optimistic `version` starting at 1.

Required indexes:

```text
ix_vendor_created_by_user
ix_vendor_status
```

Initial status is `PENDING`. This phase does not set `ACTIVE`.

### `vendor.vendor_country`

Implement:

- UUID `vendor_country_id`.
- Foreign key to `vendor.vendor` with `ON DELETE RESTRICT`.
- `country_id` UUID without a market-table foreign key.
- Constrained `onboarding_status`.
- Optional business and tax registration numbers.
- `verification_status` defaulting to `PENDING`.
- `selling_enabled` and `payout_enabled` defaulting to false.
- Optional terms version and acceptance time.
- Nullable approval and suspension columns.
- UTC timestamps and optimistic `version` starting at 1.

Required uniqueness and indexes:

```text
uq_vendor_country_vendor_country (vendor_id, country_id)
ix_vendor_country_country_status (country_id, onboarding_status)
```

Initial onboarding status is `DRAFT`. This phase does not move a row to
`SUBMITTED`, `APPROVED`, or `REJECTED`.

### `vendor.vendor_member`

Implement:

- UUID `vendor_member_id`.
- Foreign keys to `vendor.vendor` and `iam.user_account`, both
  `ON DELETE RESTRICT`.
- Constrained `status`.
- Nullable `invited_at` and `accepted_at`.
- UTC `created_at`.

Required uniqueness:

```text
uq_vendor_member_vendor_user (vendor_id, user_id)
```

This phase does not insert members. The table exists so approval and invitation
can be added without another foundation migration. A database check rejects
`ACTIVE` when `accepted_at` is null.

## Domain types

Define string enums:

- `VendorType`: `COMPANY`, `INDIVIDUAL`, `SOLE_TRADER`.
- `VendorStatus`: `PENDING`, `ACTIVE`, `SUSPENDED`, `CLOSED`.
- `OnboardingStatus`: `DRAFT`, `SUBMITTED`, `REVIEW`, `APPROVED`, `REJECTED`.
- `MemberStatus`: `INVITED`, `ACTIVE`, `SUSPENDED`.
- `VerificationStatus`: `PENDING`, `VERIFIED`, `REJECTED`.

Persist them as constrained `varchar` values, not PostgreSQL enums.

## Repository contract

`VendorRepository` exposes:

```python
get_vendor(vendor_id: UUID, *, for_update: bool = False) -> Vendor | None
get_country(vendor_country_id: UUID, *, for_update: bool = False) -> VendorCountry | None
list_countries_for_vendor(vendor_id: UUID) -> list[VendorCountry]
add_vendor(vendor: Vendor) -> None
add_country(country: VendorCountry) -> None
```

Repository methods do not commit. They do not grant roles. They do not write
audit events.

## Service contract

`VendorFoundationService` supports one internal use case:

```python
create_draft_organization(
    *,
    created_by_user_id: UUID,
    legal_name: str,
    display_name: str,
    vendor_type: VendorType,
    registration_country_id: UUID,
    market_country_id: UUID,
) -> VendorCountry
```

Rules:

- The caller must pass an existing account id. This phase does not check the
  account status. The submit phase does.
- The service inserts one `PENDING` vendor and one `DRAFT` vendor country.
- `registration_country_id` and `market_country_id` may differ. The market
  country is the application country. The registration country is the home
  country on the organization.
- `selling_enabled` and `payout_enabled` stay false.
- No membership row is inserted. The applicant is `created_by_user_id` only.
- No role assignment is created.
- The service does not commit. The caller owns the transaction.
- Public exceptions are not part of this phase because there is no route.

## Explicit non-goals

- No `app/api` changes.
- No document, payout, store, warehouse, or catalog tables.
- No changes to `public.products`.
- No `VENDOR_OWNER`, `VENDOR_STAFF`, or `STORE_STAFF` rows.
- No new permissions on `COUNTRY_OPERATIONS`.
- No row-level security policies. Those arrive with the review routes.
- No Seller Center client and no second login.

## Test-first implementation sequence

1. Add failing model tests for schema names, UUID ids, and status constraints.
2. Implement the three models.
3. Add the four Alembic revisions and run migration integration tests.
4. Add failing repository tests for insert and unique country lookup.
5. Implement repository methods without commits.
6. Add failing service tests for the draft organization rules.
7. Implement the foundation service.
8. Run the vendor unit tests and the migration tests.

## Required tests

Unit tests:

- Vendor type and status reject unknown values.
- Draft creation sets vendor `PENDING` and country `DRAFT`.
- Selling and payout flags are false.
- Version starts at 1.
- The service does not call an assignment port.
- The service does not commit.

PostgreSQL integration tests:

- Upgrade from `20261012_0018` creates schema `vendor` and the three tables.
- Downgrade returns the database to `20261012_0018`.
- Two country rows for the same vendor and country conflict.
- Two country rows for the same vendor and different countries succeed.
- Two memberships for the same vendor and user conflict.
- An `ACTIVE` membership without `accepted_at` is rejected.
- Deleting a user account that created a vendor is restricted.
- Deleting a vendor that still has a country row is restricted.
- Generated SQL uses `vendor.vendor`, not `public.vendor`.

## Security checks

- Model `repr` omits registration numbers and tax numbers.
- Tests do not insert a role assignment.
- The migration does not grant database privileges beyond the table owner used
  by existing Alembic migrations.

## Completion criteria

Phase 1 is complete when:

- A database at `20261012_0018` upgrades to `20261028_0022` and downgrades
  cleanly.
- Organization, country, and member invariants pass unit and integration tests.
- Alembic sees the new models.
- No API route, access catalog row, or public product table changed.

## Handoff to Phase 2

Phase 2 consumes:

- `Vendor`, `VendorCountry`, `VendorMember`, and their enums.
- `VendorRepository` and `VendorFoundationService`.
- The unique vendor-country and vendor-user keys.

Phase 2 adds document metadata, submit and review services, the owner badge,
and the staff invitation. It is the first phase allowed to seed
`VENDOR_OWNER` and the `vendor.application.review` permissions.

Do not start Phase 2 until this migration has been tested on PostgreSQL.
