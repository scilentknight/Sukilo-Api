# Phase 2 — Seller Application, Review, and Staff Invite

## Goal

Let a verified account submit one country application, let country operations
approve or reject it, and let an approved owner invite staff.

Approval creates the owner membership and the `VENDOR_OWNER` assignment in the
same transaction. Staff become `VENDOR_STAFF` only after they accept. This
phase does not create stores, warehouses, payout accounts, or catalog rows.
`selling_enabled` stays false after approval.

## Prerequisites

- Phase 1 tables `vendor.vendor`, `vendor.vendor_country`, and
  `vendor.vendor_member` are migrated and tested.
- `AccountRepository.get_by_id` can lock the applicant and the invited user.
- `CountryReader` resolves the market and reports whether seller onboarding is
  enabled.
- `AuditWriter.stage` writes through the caller's transaction. A no-op writer
  is not acceptable for approval.
- Access assignments can be granted for `VENDOR` scope with `scope_id` equal
  to `vendor_id` and a required `country_id`.
- The public assignment route still rejects `SUPER_ADMIN`.

## Deliverables

- `vendor.vendor_document` for file metadata and an opaque storage key.
- `vendor.vendor_invitation` for single-use hashed invite tokens.
- Seed `VENDOR_OWNER`, `VENDOR_STAFF`, and the application permissions.
- Application draft, update, and submit services.
- Country review queue, approve, and reject services.
- Staff invite, accept, and revoke services.
- Routes under `/api/v1/vendor`.
- Tests that a second country, a foreign reviewer, and a guessed document id
  are denied.

## Planned file ownership

Create:

```text
app/modules/vendor/models/vendor_document.py
app/modules/vendor/models/vendor_invitation.py
app/modules/vendor/schemas.py
app/modules/vendor/applications.py
app/modules/vendor/reviews.py
app/modules/vendor/invitations.py
app/ports/document_storage.py
app/api/v1/endpoints/vendor_applications.py
app/api/v1/endpoints/vendor_reviews.py
app/api/v1/endpoints/vendor_staff.py
tests/unit/vendor/test_application_service.py
tests/unit/vendor/test_review_service.py
tests/unit/vendor/test_invitation_service.py
tests/integration/vendor/test_vendor_endpoints.py
tests/security/test_vendor_country_scope.py
alembic/versions/20261028_0023_create_vendor_document.py
alembic/versions/20261028_0024_create_vendor_invitation.py
alembic/versions/20261028_0025_seed_vendor_access.py
```

Modify:

```text
app/models/__init__.py
app/api/v1/router.py
app/modules/access/catalog.py
app/modules/vendor/repository.py
app/modules/vendor/types.py
```

`catalog.py` gains the new role and permission definitions. The migration
inserts those same deterministic ids. Do not edit the already applied
`20261012_0013` seed.

## Database design

### `vendor.vendor_document`

Columns follow `architecture.md`. `storage_key` replaces `media_id` until
`catalog.media_asset` exists. `document_number` is optional and must already
be masked by the caller. `verification_status` is `PENDING`, `VERIFIED`, or
`REJECTED`.

Index:

```text
ix_vendor_document_country (vendor_country_id, document_type)
```

### `vendor.vendor_invitation`

| Column | Rule |
| --- | --- |
| `invitation_id` | UUID primary key. |
| `vendor_id` | Foreign key to `vendor.vendor`. |
| `country_id` | Market UUID. |
| `email` | Normalized email of the invited person. |
| `user_id` | Nullable until an account exists. |
| `token_hash` | Hash of the single-use token. Unique. |
| `expires_at` | Required. |
| `accepted_at` | Null until acceptance. |
| `revoked_at` | Null until the owner withdraws it. |
| `created_by` | Owner account. |
| `created_at` | UTC. |

Do not store the raw token. The response that creates the invite returns the
raw token once. Later reads return only status and expiry.

### Access seed

Add roles with the existing catalog namespace:

- `VENDOR_OWNER`, scope `VENDOR`.
- `VENDOR_STAFF`, scope `VENDOR`.

Add permissions:

- `vendor.application.read`
- `vendor.application.submit`
- `vendor.application.review`
- `vendor.application.approve`
- `vendor.staff.invite`
- `vendor.staff.revoke`

Map review and approve permissions to `COUNTRY_OPERATIONS`. Map invite and
revoke to `VENDOR_OWNER`. Map every new permission onto `SUPER_ADMIN`. Do not
give `COUNTRY_ADMIN` the approve permission. Do not seed `STORE_STAFF`.

## Routes

All routes require `Authorization: Bearer`.

| Method and path | Caller | Result |
| --- | --- | --- |
| `POST /api/v1/vendor/applications` | Active account | Draft vendor and draft country, or a new country on an existing vendor. |
| `PATCH /api/v1/vendor/applications/{id}` | Applicant | Updates a `DRAFT` or `REVIEW` row. Requires `expected_version`. |
| `POST /api/v1/vendor/applications/{id}/documents` | Applicant | Stores metadata and the storage key. |
| `POST /api/v1/vendor/applications/{id}/submit` | Applicant | Moves `DRAFT` or `REVIEW` to `SUBMITTED`. |
| `GET /api/v1/vendor/applications/{id}` | Applicant or same-country reviewer | Returns the application without storage keys. |
| `GET /api/v1/vendor/reviews` | Country reviewer | Lists `SUBMITTED` and `REVIEW` rows for one country code. |
| `POST /api/v1/vendor/reviews/{id}/approve` | Country reviewer | Approves and grants `VENDOR_OWNER`. |
| `POST /api/v1/vendor/reviews/{id}/reject` | Country reviewer | Rejects with a reason. |
| `POST /api/v1/vendor/staff/invitations` | Vendor owner | Creates an invite for that vendor and country. |
| `POST /api/v1/vendor/staff/invitations/{id}/accept` | Invited account | Activates membership and grants `VENDOR_STAFF`. |
| `POST /api/v1/vendor/staff/members/{id}/revoke` | Vendor owner | Suspends the member and revokes the assignment. |

Approve and reject require `X-Step-Up-Grant`. Approve uses action class
`vendor.application.approve`. Reject uses `vendor.application.reject`.

Country comes from `country_code` on the request and is resolved with
`CountryReader`. The service never trusts a client `country_id`.

## Service rules

### Submit

- The account is `ACTIVE`.
- The country allows seller onboarding.
- Legal name, display name, vendor type, and the country's required document
  types are present.
- `terms_version` is the active seller terms for that country, and
  `terms_accepted_at` is set.
- Status becomes `SUBMITTED`.
- No role is granted.

### Approve

Lock `vendor_country` and the applicant account.

- Status must be `SUBMITTED` or `REVIEW`.
- The reviewer assignment country equals `vendor_country.country_id`.
- Set `APPROVED`, `approved_at`, and `approved_by`.
- If `vendor.status` is `PENDING`, set it to `ACTIVE`.
- Insert an `ACTIVE` member for the applicant with `accepted_at` set.
- Grant `VENDOR_OWNER` through the assignment port.
- Leave `selling_enabled` and `payout_enabled` false.
- Stage one audit event. The payload contains ids and the decision only.

If any step fails, roll back the decision, the member, and the badge.

### Reject

- A non-empty reason is required.
- Set `REJECTED`.
- Do not insert a member and do not grant a badge.

### Invite and accept

- Only the owner of that vendor and country may invite.
- Create the invitation whether or not an account already uses the email.
  Attach `user_id` when the account exists. Do not return a different message
  for an unknown email.
- Accept requires the authenticated user to match `user_id`, or to match the
  invitation email when `user_id` was empty.
- Accept sets the member to `ACTIVE`, sets `accepted_at`, consumes the token,
  and grants `VENDOR_STAFF`.
- The invited person and the new owner both log in again after the grant
  because `auth_version` changes.

### Request more information

`POST /api/v1/vendor/reviews/{id}/request-info` sets `REVIEW` and stores the
reason on the audit event. The applicant may edit and submit again. This is
not a new status value.

## Errors

- `401` for a missing, expired, or stale token.
- `403` for a reviewer outside the application country or an owner acting on
  another vendor.
- `404` for another seller's application, document, or invitation.
- `409` when `expected_version` is stale or the status is not editable.
- `422` when a required document type is missing.

## Required tests

- Submit does not create `VENDOR_OWNER`.
- Approval creates the member and the owner assignment, or rolls both back.
- Approval does not set `selling_enabled`.
- A Nepal reviewer cannot approve a UAE application.
- The applicant cannot approve their own application.
- Reject grants nothing.
- Accepting an invite grants `VENDOR_STAFF` for that vendor and country only.
- A second accept of the same token fails.
- Document responses omit `storage_key`.
- Audit payloads omit document numbers.

## Security checks

- Invite tokens are stored only as hashes.
- Guessed document ids return `404`.
- Review routes require step-up.
- `SUPER_ADMIN` is still not grantable from `/api/v1/access/assignments`.

## Completion criteria

Phase 2 is complete when:

- A verified account can save and submit one country application.
- Country operations can approve or reject only inside its country.
- Approval leaves one owner badge and `selling_enabled` false.
- Staff access exists only after acceptance.
- No store, payout, warehouse, or catalog table was created.

## Handoff to Phase 3

Phase 3 consumes an approved vendor country, an active owner membership, and
the `VENDOR_OWNER` assignment. It adds payout, store, translation, and
warehouse records, and it is the first phase that may set `selling_enabled`
after those records pass the go-live checks.

Do not start Phase 3 until approval rollback and cross-country denial tests
pass on PostgreSQL.
