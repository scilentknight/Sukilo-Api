# Phase 4 — Customer Profile, Preferences, Consent, and Addresses

## Goal

Deliver individual-customer self-service APIs for account projection,
identifier-change verification, market/language preferences, versioned consent,
and country-scoped addresses.

This phase satisfies FR-CUS-005 and the account/preference requirements in
sections 3.1 and 5.1 of the product specification.

## Prerequisites

- Phase 2 authentication and verification workflows are complete.
- Phase 3 ownership policies and authenticated FastAPI dependency are complete.
- `CountryReader` can validate enabled country, locale, currency, geography,
  address rules, and active policy versions.
- Country owners have supplied active terms/privacy policy versions.

## Deliverables

- `iam.user_preference`, `iam.consent_record`, and customer-owned
  `iam.address`.
- `/me`, preference, consent, and address APIs.
- Verified email/phone replacement workflow that reuses identity services.
- Country-aware address and preference validation.
- Transactional default shipping/billing behavior.
- Optimistic-concurrency and ownership security tests.

## Planned file ownership

Create:

```text
app/modules/customers/__init__.py
app/modules/customers/models.py
app/modules/customers/schemas.py
app/modules/customers/repository.py
app/modules/customers/service.py
app/modules/customers/ports.py
app/api/v1/endpoints/customers.py
tests/unit/customers/test_preference_service.py
tests/unit/customers/test_consent_service.py
tests/unit/customers/test_address_service.py
tests/integration/customers/test_customer_endpoints.py
tests/integration/customers/test_address_defaults.py
tests/security/test_customer_ownership.py
alembic/versions/<revision>_add_iam_customer_data.py
```

Modify:

```text
app/modules/identity/service.py
app/modules/identity/otp.py
app/modules/access/policies.py
app/api/v1/router.py
app/models/__init__.py
tests/conftest.py
```

## Database changes

### `iam.user_preference`

Columns:

- `user_id UUID PRIMARY KEY`
- `preferred_country_id UUID NULL`
- `preferred_locale_code VARCHAR(12) NULL`
- `preferred_currency_code CHAR(3) NULL`
- `marketing_email BOOLEAN NOT NULL DEFAULT false`
- `marketing_sms BOOLEAN NOT NULL DEFAULT false`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`
- `version INTEGER NOT NULL DEFAULT 1`

The account foreign key uses `ON DELETE RESTRICT`.

Country, locale, and currency IDs are validated through `CountryReader`. Add
physical foreign keys when the market schema is implemented; until then, the
service contract and integration tests enforce referential validity.

Marketing booleans are materialized current communication settings. They are
updated only by `ConsentService` from append-only consent records. Preference
APIs cannot toggle them directly.

### `iam.consent_record`

Columns:

- `consent_id UUID PRIMARY KEY`
- `user_id UUID NOT NULL`
- `country_id UUID NOT NULL`
- `consent_type VARCHAR(64) NOT NULL`
- `policy_version VARCHAR(32) NOT NULL`
- `granted BOOLEAN NOT NULL`
- `source VARCHAR(32) NOT NULL`
- `recorded_at TIMESTAMPTZ NOT NULL`
- `correlation_id UUID NOT NULL`

Initial consent types:

- `TERMS`
- `PRIVACY`
- `MARKETING_EMAIL`
- `MARKETING_SMS`
- `ANALYTICS`

Initial sources:

- `WEB`
- `APP`
- `ADMIN`
- `SYSTEM`

Records are append-only. No application update or delete method exists. Current
state is the latest record ordered by `(recorded_at, consent_id)` for one user,
country, and type.

Required indexes:

```text
ix_consent_user_country_type_latest
ix_consent_country_type_recorded
```

### `iam.address`

Columns:

- `address_id UUID PRIMARY KEY`
- `user_id UUID NOT NULL`
- `country_id UUID NOT NULL`
- `geography_area_id UUID NULL`
- `recipient_name VARCHAR(160) NOT NULL`
- `phone_e164 VARCHAR(24) NOT NULL`
- `address_line_1 VARCHAR(255) NOT NULL`
- `address_line_2 VARCHAR(255) NULL`
- `landmark VARCHAR(255) NULL`
- `city_text VARCHAR(120) NULL`
- `postal_code VARCHAR(32) NULL`
- `latitude NUMERIC(10,7) NULL`
- `longitude NUMERIC(10,7) NULL`
- `address_type VARCHAR(24) NOT NULL`
- `is_shipping BOOLEAN NOT NULL DEFAULT true`
- `is_billing BOOLEAN NOT NULL DEFAULT false`
- `is_default_shipping BOOLEAN NOT NULL DEFAULT false`
- `is_default_billing BOOLEAN NOT NULL DEFAULT false`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`
- `deleted_at TIMESTAMPTZ NULL`
- `version INTEGER NOT NULL DEFAULT 1`

Address types are `HOME`, `OFFICE`, and `OTHER`. `WAREHOUSE` belongs to the
future vendor domain and is not accepted by customer APIs.

Constraints:

- At least one of `is_shipping` or `is_billing` is true.
- Default shipping implies `is_shipping`.
- Default billing implies `is_billing`.
- Latitude is between -90 and 90; longitude is between -180 and 180.
- Deleted rows cannot remain defaults.
- User/account foreign key uses `ON DELETE RESTRICT`.

Required indexes:

```text
ix_address_user_country_active
uq_address_default_shipping_user_country
uq_address_default_billing_user_country
```

The default indexes are partial unique indexes over active default rows.

Addresses use soft deletion because active carts or non-finalized workflows may
still reference a selected address. Future orders store an immutable snapshot
and do not rely on this row.

## Customer projection

`GET /me` composes:

- Stable `user_id`.
- Account status.
- Masked email and phone plus verification flags.
- Account creation and last-login timestamps.
- Current preferences.
- Active session ID/device indicator.

It never returns password hash, auth version, raw session metadata, role
assignment internals, consent history, or full audit data.

No duplicate customer ID is created. The projection uses the authenticated
`user_account`.

## Identifier changes

`PATCH /me` accepts only:

```json
{
  "new_email": "new@example.com",
  "new_phone_e164": null,
  "country_code": "NP",
  "locale_code": "en-NP"
}
```

It starts, but does not directly apply, an identifier change. It calls the
identity module to:

1. Require recent password authentication.
2. Normalize and check the candidate identifier.
3. Create a purpose-bound verification challenge.
4. Verify ownership through the Phase 2 OTP flow.
5. Return a one-time `CHANGE_EMAIL` or `CHANGE_PHONE` auth grant bound to the
   current user, session, and normalized candidate fingerprint.

`POST /me/identifier-change/confirm` accepts the opaque grant and candidate
identifier. It consumes the matching auth grant while replacing the identifier,
setting the verification timestamp, incrementing `auth_version`, revoking other
sessions, and staging the redacted audit event in one transaction. Notifications
to old and new destinations are queued after persistence.

Changing email and phone together is rejected; each identifier has an
independent verification flow. The account may not remove its final usable
login identity.

## Preferences service

`PreferenceService.set_preferences()`:

1. Requires the authenticated account.
2. Resolves country code to stable country ID.
3. Confirms country is active for preference selection.
4. Confirms locale is enabled for that country.
5. Confirms display currency is enabled for that country.
6. Creates or optimistically updates the single preference row.
7. Emits a redacted preference-change event.

Changing locale never changes country. Changing country never silently changes
locale or currency; the request must provide a valid complete combination when
the existing values are incompatible.

The preference is not authorization. Checkout and every commercial request
still use trusted route/request country context.

Changing from Nepal to UAE updates the same account's preference; it does not
create or migrate an account. Before country-scoped commerce, the customer must
accept the active UAE policy versions and provide an eligible UAE address.
Nepal consent, addresses, carts, orders, and any administrative/vendor
assignments remain scoped to Nepal and are not copied to UAE.

## Consent service

`ConsentService.record()`:

1. Requires explicit country and active policy version.
2. Requires the supplied policy version to equal the active version returned by
   `CountryReader`; acknowledgements of superseded versions use a separately
   reviewed administrative migration, not the customer API.
3. Inserts a new record even when it repeats the previous value, preserving
   evidence of the explicit action.
4. Updates marketing preference projections in the same transaction for
   marketing consent types.
5. Stages a redacted audit event through the same database transaction.

Rules:

- Transactional notifications do not depend on marketing consent.
- Terms/privacy withdrawal may trigger account restrictions defined by country
  policy, but never silently deletes the account.
- An administrator recording consent uses `source=ADMIN`, requires a dedicated
  permission, and records original actor and reason.
- Consent timestamps come from the server.

## Address service

Expose:

```python
list_addresses(user_id: UUID, country_id: UUID) -> list[AddressView]
create_address(user_id: UUID, country_id: UUID, command: CreateAddress) -> AddressView
get_address(user_id: UUID, country_id: UUID, address_id: UUID) -> AddressView
update_address(user_id: UUID, country_id: UUID, address_id: UUID, command: UpdateAddress) -> AddressView
delete_address(user_id: UUID, country_id: UUID, address_id: UUID, expected_version: int) -> None
set_default(user_id: UUID, country_id: UUID, address_id: UUID, kind: DefaultKind) -> AddressView
```

Validation:

- Resolve and validate the address country.
- Normalize delivery phone independently from login phone.
- If geography ID is present, require it to belong to the same country.
- Apply country policy for postal code, required administrative levels, and
  coordinate availability.
- Reject unsupported or unknown fields rather than persisting arbitrary JSON.
- Validate string lengths before persistence.

Default transaction:

1. Acquire a transaction-scoped PostgreSQL advisory lock derived from stable
   `(user_id, country_id, default_kind)` values. This also serializes concurrent
   first-address inserts when no row exists to lock.
2. Resolve and authorize the address owner and exact country.
3. Lock active address rows for that user and country.
4. Clear the existing default for the requested kind.
5. Set the selected address as the default.
6. Commit with optimistic version changes.

When the first shipping or billing address is created for a country, make it
the default for that supported kind. Deleting a default promotes the most
recent eligible active address in the same transaction, or leaves no default
when none exists.

## API contracts

All routes require `require_authenticated` and enforce the authenticated
`user_id`; no route accepts a user ID from the client.

### Profile and preferences

- `GET /api/v1/me`
- `PATCH /api/v1/me`
- `POST /api/v1/me/identifier-change/confirm`
- `GET /api/v1/me/preferences`
- `PUT /api/v1/me/preferences`

Preference update:

```json
{
  "country_code": "AE",
  "locale_code": "ar-AE",
  "currency_code": "AED",
  "expected_version": 3
}
```

### Consent

- `GET /api/v1/me/consents?country_code=NP`
- `POST /api/v1/me/consents`

Consent input:

```json
{
  "country_code": "NP",
  "consent_type": "MARKETING_EMAIL",
  "policy_version": "2026-09",
  "granted": false
}
```

The list endpoint returns current state plus evidence timestamps by default.
Full history requires an explicit `history=true` query and cursor pagination.

### Addresses

- `GET /api/v1/me/addresses?country_code=NP`
- `POST /api/v1/me/addresses`
- `GET /api/v1/me/addresses/{address_id}?country_code=NP`
- `PATCH /api/v1/me/addresses/{address_id}?country_code=NP`
- `DELETE /api/v1/me/addresses/{address_id}?country_code=NP`
- `POST /api/v1/me/addresses/{address_id}/default?country_code=NP`

Updates and deletes require `expected_version`. Stale writes return `409` with
`CUSTOMER_VERSION_CONFLICT`.

The default command specifies `SHIPPING` or `BILLING`. Every operation resolves
the supplied country code through `CountryReader` and requires the row's
`country_id` to match; a mismatch returns not found.

## Privacy and response shaping

- Mask identifiers in `/me`; return full values only through a separately
  authorized security settings endpoint if required later.
- Address responses are available only to the owner and controlled future
  fulfillment views.
- Never expose address data in list logs, traces, metrics, or audit diffs.
- Audit payloads describe changed field names, address ID, and country ID, not
  recipient/contact/address values.
- Coordinates are optional and receive the same protection as address PII.
- Soft-deleted addresses are absent from normal API responses.

## Test-first implementation sequence

1. Add migration/model tests for customer tables and default constraints.
2. Implement models and migration.
3. Add failing preference combination and optimistic-version tests.
4. Implement preference repository/service and endpoints.
5. Add failing append-only consent and projection tests.
6. Implement consent repository/service and endpoints.
7. Add failing address ownership, country, and default-concurrency tests.
8. Implement address repository/service and endpoints.
9. Add failing identifier-change verification tests.
10. Connect `/me` changes to the identity verification service.
11. Add PII/log/audit redaction tests.
12. Run unit, PostgreSQL integration, concurrency, and security test sets.

## Required tests

Preferences:

- Country, locale, and currency valid combination succeeds.
- Locale enabled in another country fails.
- Country change cannot retain an incompatible locale/currency silently.
- Preference update with stale version returns conflict.
- Marketing booleans cannot be changed by preference API.
- Preference does not grant country access.

Consent:

- Repeated grant creates a second evidence record.
- Withdrawal creates a record; it does not update/delete history.
- Current state resolves deterministically for equal timestamps.
- Marketing consent updates the materialized preference atomically.
- Transactional notification eligibility ignores marketing withdrawal.
- Invalid policy version and client timestamp are rejected.

Addresses:

- A user can create, read, update, and soft-delete their own address.
- Another user receives not found and no ownership information.
- Geography from another country is rejected.
- Invalid coordinates and address/default combinations fail.
- First eligible address becomes the appropriate default.
- Concurrent default changes leave exactly one default per kind/country.
- Concurrent first-address creation is serialized by the advisory lock and
  leaves exactly one default.
- Supplying another country for an owned address returns not found.
- Deleting a default promotes an eligible replacement atomically.
- Stale update/delete versions return conflict.
- Vendor-only `WAREHOUSE` type is rejected.

Profile:

- `/me` returns masked identifiers and no sensitive internal fields.
- Identifier change does not apply before verification.
- Verified change increments `auth_version` and revokes other sessions.
- Final login identity cannot be removed.
- Duplicate candidate identifier returns a safe conflict response.

Privacy:

- Captured logs and traces contain no recipient, phone, lines, postal code, or
  coordinates.
- Audit events contain changed field names but no address values.
- List pagination cannot cross the authenticated owner.

## Completion criteria

Phase 4 is complete when:

- FR-CUS-005 has linked passing API and ownership tests.
- Customer identity remains `user_account`; no duplicate customer master exists.
- Preferences validate market/locale/currency combinations without granting
  authorization.
- Consent history is append-only and marketing projections are atomic.
- Address ownership, country validation, optimistic locking, and default
  uniqueness pass concurrent PostgreSQL tests.
- `/me` and audit/log output satisfy PII minimization checks.
- Identifier replacements require successful purpose-bound verification.

## Handoff to Phase 5

Phase 5 receives all IAM and customer tables plus:

- Trusted auth and country contexts.
- Reusable authorization policies.
- Security-sensitive audit events.
- Complete cross-account and cross-country application-level tests.

RLS is enabled only after these application controls pass.
