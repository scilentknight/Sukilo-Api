# Phase 1 — IAM Foundation and Canonical Accounts

## Goal

Create the `iam` database foundation, canonical account identity, package
boundaries, and narrow country/audit contracts required by later phases.

This phase changes backend structure and persistence only. It does not expose
registration, login, customer, or role-management APIs.

## Prerequisites

- PostgreSQL is available through the existing SQLAlchemy/Alembic setup.
- Python 3.12 and the repository versions of FastAPI, SQLAlchemy, Pydantic, and
  Alembic remain the baseline.
- `architecture.md` is accepted as the governing design.

## Deliverables

- PostgreSQL schema `iam` and extension `citext`.
- SQLAlchemy models for `iam.user_account` and `iam.auth_identity`.
- Identity repository and service contracts.
- Typed `CountryReader` and `AuditWriter` ports.
- UUID, clock, and normalization helpers needed by this domain.
- Separate reversible Alembic revisions for database prerequisites and each
  table.
- Unit and PostgreSQL integration tests for all phase invariants.

## Planned file ownership

Create:

```text
app/modules/__init__.py
app/modules/identity/__init__.py
app/modules/identity/models/__init__.py
app/modules/identity/models/user_account.py
app/modules/identity/models/auth_identity.py
app/modules/identity/repository.py
app/modules/identity/service.py
app/modules/identity/types.py
app/ports/__init__.py
app/ports/country.py
app/ports/audit.py
app/core/transactions.py
app/core/identifiers.py
tests/unit/identity/test_identifiers.py
tests/unit/identity/test_account_service.py
tests/integration/identity/test_account_repository.py
tests/integration/identity/test_identity_constraints.py
alembic/versions/20261012_0003_create_iam_schema.py
alembic/versions/20261012_0004_create_iam_user_account.py
alembic/versions/20261012_0005_create_iam_auth_identity.py
```

Modify:

```text
app/models/__init__.py
alembic/env.py
tests/conftest.py
```

`app/models/__init__.py` imports the new metadata so Alembic sees the tables.
It does not take ownership of the models.

## Database design

### Migration order

1. Revision `0003` enables `citext` and creates PostgreSQL schema `iam`.
2. Revision `0004` creates `iam.user_account` and its indexes.
3. Revision `0005` creates `iam.auth_identity` and its index.
4. Seed no users, roles, countries, or credentials.

Downgrades run in reverse dependency order: authentication identity, account,
then the empty `iam` schema. They do not remove `citext`, because another schema
may use the extension.

### `iam.user_account`

Implement the columns and rules in `architecture.md`, including:

- UUID `user_id` generated in the application.
- `CITEXT` email and normalized E.164 phone.
- Optional password hash and verification timestamps.
- Constrained account status.
- `auth_version` and optimistic `version`.
- UTC creation/update timestamps.

Required indexes:

```text
uq_user_account_email_present
uq_user_account_phone_present
ix_user_account_status
```

The unique indexes include only non-null values. Closed accounts retain their
stable identifiers and continue to occupy normalized email/phone values until a
later, audited pseudonymization policy releases them.

### `iam.auth_identity`

Implement:

- UUID primary key.
- Foreign key to `iam.user_account` with `ON DELETE RESTRICT`.
- Provider and provider subject.
- Unique `(provider, provider_subject)`.
- Index on `user_id`.

The phase permits only the `LOCAL` provider through the service API. The table
shape remains ready for social identity providers.

## Domain types

Define string enums for:

- `AccountStatus`: `PENDING`, `ACTIVE`, `SUSPENDED`, `CLOSED`.
- `AuthProvider`: initially `LOCAL`.

Database constraints use stable string values rather than PostgreSQL enum
types, allowing controlled lifecycle expansion through migrations.

This enum rule applies only to closed technical state sets. Roles, permissions,
countries, and configurable business definitions must use database tables and
stable codes rather than Python or PostgreSQL enums.

Define value objects or normalization functions:

```python
normalize_email(value: str) -> str
normalize_phone_e164(value: str, default_region: str | None = None) -> str
new_uuid() -> UUID
utc_now() -> datetime
```

Email normalization trims surrounding whitespace and applies Unicode-safe
domain handling. `CITEXT` supplies case-insensitive database comparison.

Phone normalization rejects values that cannot be converted to a valid E.164
number. The country context may provide a default dialing region, but the stored
value is always explicit E.164.

## Repository contract

`AccountRepository` exposes focused methods:

```python
get_by_id(user_id: UUID, *, for_update: bool = False) -> UserAccount | None
get_by_email(email: str) -> UserAccount | None
get_by_phone(phone_e164: str) -> UserAccount | None
add(account: UserAccount) -> None
add_identity(identity: AuthIdentity) -> None
identifier_exists(email: str | None, phone_e164: str | None) -> bool
```

Repository methods do not commit. The service owns the transaction boundary.
Repository code never returns password hashes in API schemas or log data.

## Service contract

`AccountService` initially supports internal use cases:

```python
create_pending_local_account(
    *,
    email: str,
    phone_e164: str | None,
    password_hash: str,
) -> UserAccount

get_account(user_id: UUID) -> UserAccount

assert_account_can_authenticate(account: UserAccount) -> None
```

Rules:

- Initial self-registration requires a normalized email and password. The
  nullable schema fields preserve future phone-first, passwordless, and social
  identity options, but public services do not enable those forms in this
  phase.
- A local account with a password creates one `LOCAL` auth identity in the same
  transaction.
- Duplicate identifiers map to a domain conflict, not a raw database error.
- `PENDING` may verify identifiers but cannot use authenticated marketplace
  APIs.
- `SUSPENDED` and `CLOSED` fail authentication.
- Public service exceptions contain stable machine codes and no PII.

## External ports

### `CountryReader`

Define a protocol in `app/ports/country.py`:

```python
resolve_country(code: str) -> CountryReference
is_operation_enabled(country_id: UUID, operation: str) -> bool
is_locale_enabled(country_id: UUID, locale_code: str) -> bool
is_currency_enabled(country_id: UUID, currency_code: str) -> bool
geography_belongs_to_country(country_id: UUID, geography_area_id: UUID) -> bool
get_address_policy(country_id: UUID) -> AddressPolicy
get_active_policy_version(country_id: UUID, consent_type: str) -> str
```

`CountryReference` contains only `country_id`, ISO alpha-2 `code`, and lifecycle
status. The interface has an in-memory fake for tests. This phase does not
create market tables.

### `AuditWriter`

Define:

```python
stage(event: AuditEventInput, transaction: DatabaseTransaction) -> None
```

`AuditEventInput` includes actor, action, resource, optional country/vendor
scope, redacted before/after data, security hashes, and correlation ID.
`DatabaseTransaction` wraps the active SQLAlchemy session; the audit adapter
inserts an audit/outbox row through that session and never commits
independently. Provide a no-op development adapter and a recording test fake.
Production wiring must replace the no-op before Phase 2 endpoints are enabled.

## Metadata and migration configuration

- Register the new model metadata exactly once.
- Preserve one SQLAlchemy `Base` unless a later repository-wide migration
  deliberately introduces separate registries.
- Ensure Alembic includes schema-qualified tables and compares server defaults.
- Integration tests assert that generated SQL targets `iam.user_account` and
  `iam.auth_identity`, not the default schema.
- Migration tests execute upgrade and downgrade against disposable PostgreSQL.
  SQLite is not an acceptable substitute for `CITEXT`, partial indexes, or
  schemas.

## Test-first implementation sequence

1. Add failing tests for email and E.164 normalization.
2. Implement normalization helpers.
3. Add failing model tests for names, schema, UUID IDs, and status constraints.
4. Implement account and identity models.
5. Add the three Alembic revisions and run migration integration tests.
6. Add failing repository tests for account lookup and uniqueness conflicts.
7. Implement repository methods without commits.
8. Add failing service tests for identifier requirements, duplicates, and
   account status.
9. Implement the internal account service.
10. Add test fakes and contracts for country and audit ports.
11. Run all unit and PostgreSQL integration tests.

## Required tests

Unit tests:

- Email normalization is deterministic.
- Invalid/ambiguous phone input is rejected.
- Account creation requires email and password.
- Service accepts email/password with or without an optional phone.
- Phone-first and passwordless registration are rejected until a later phase
  defines complete verification and recent-authentication policies.
- Service rejects duplicate identifiers with the same public conflict code.
- Pending, suspended, and closed status rules are distinct.
- Service does not commit repository transactions.

PostgreSQL integration tests:

- Two case variants of the same email conflict.
- Two equal non-null phones conflict.
- Multiple null emails and phones are allowed at the column level.
- Duplicate provider/subject conflicts.
- Deleting an account with an identity is restricted.
- Invalid status is rejected.
- Optimistic version starts at one.
- Migration upgrade and downgrade complete from a clean database.

## Security checks

- No model `repr` includes `password_hash`.
- No service exception includes email, phone, or a credential hash.
- SQL query logging is disabled or redacted in production configuration before
  credential-bearing endpoints are introduced.
- The test audit fake rejects payload keys named `password`, `password_hash`,
  `token`, `refresh_token`, or `otp`.

## Completion criteria

Phase 1 is complete when:

- A clean PostgreSQL database upgrades and contains the expected `iam` schema,
  constraints, and indexes.
- Downgrade returns the database to its pre-phase state without removing shared
  extensions.
- Account and local identity invariants pass unit and integration tests.
- New models are visible to Alembic metadata.
- Country and audit contracts are usable through test fakes.
- No API routes or unrelated domain models were changed.

## Handoff to Phase 2

Phase 2 consumes:

- `UserAccount`, `AuthIdentity`, and their enums.
- `AccountRepository` and `AccountService`.
- Identifier normalization, UUID, and UTC clock helpers.
- Transaction-bound `AuditWriter` and complete country-policy interfaces.

Do not start Phase 2 until the migration has been tested on PostgreSQL.
