# Phase 3 — Scoped Access Control

## Goal

Deliver deny-by-default RBAC plus ABAC scope enforcement for global and country
administration, with reusable FastAPI authorization guards and auditable role
assignment management.

This phase satisfies FR-ID-004 and the identity/access portion of FR-ADM-001.

## Prerequisites

- Phase 2 produces a validated `AuthContext`.
- The country contract resolves stable country IDs and lifecycle state.
- The audit implementation can record privileged state changes atomically.
- The application has a controlled bootstrap process for the first super admin.

## Deliverables

- Role, permission, role-permission, and scoped-assignment tables.
- Versioned permission catalog and initial role seeds.
- Permission resolution and ABAC scope evaluation.
- Reusable FastAPI dependencies for authentication, permission, ownership, and
  step-up requirements.
- Administrative assignment APIs.
- Authorization cache invalidation tied to account `auth_version`.
- Cross-account and cross-country denial tests.

## Planned file ownership

Create:

```text
app/modules/access/__init__.py
app/modules/access/models.py
app/modules/access/schemas.py
app/modules/access/repository.py
app/modules/access/service.py
app/modules/access/policies.py
app/modules/access/dependencies.py
app/modules/access/ports.py
app/api/v1/endpoints/access.py
tests/unit/access/test_scope_validation.py
tests/unit/access/test_policy_evaluator.py
tests/unit/access/test_assignment_service.py
tests/integration/access/test_access_repository.py
tests/integration/access/test_assignment_endpoints.py
tests/security/test_cross_scope_access.py
alembic/versions/<revision>_add_iam_access_control.py
```

Modify:

```text
app/models/__init__.py
app/api/v1/router.py
app/modules/identity/models.py
app/modules/identity/service.py
app/core/auth_context.py
tests/conftest.py
```

## Database changes

### `iam.role`

Columns:

- `role_id UUID PRIMARY KEY`
- `code VARCHAR(64) NOT NULL UNIQUE`
- `name VARCHAR(120) NOT NULL`
- `scope_type VARCHAR(24) NOT NULL`
- `status VARCHAR(20) NOT NULL`
- `is_system BOOLEAN NOT NULL DEFAULT true`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

Constraints:

- Scope is `GLOBAL`, `COUNTRY`, `VENDOR`, or `STORE`.
- Status is `ACTIVE` or `RETIRED`.
- System role codes cannot be renamed or deleted through APIs.

### `iam.permission`

Columns:

- `permission_id UUID PRIMARY KEY`
- `code VARCHAR(100) NOT NULL UNIQUE`
- `description TEXT NOT NULL`
- `status VARCHAR(20) NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`

Permission codes follow `<domain>.<resource>.<action>` and are immutable after
publication. Retire obsolete permissions rather than renaming them.

### `iam.role_permission`

Columns:

- `role_id UUID NOT NULL`
- `permission_id UUID NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`

Primary key is `(role_id, permission_id)`. Foreign keys use `ON DELETE RESTRICT`.

### `iam.user_role_assignment`

Columns:

- `assignment_id UUID PRIMARY KEY`
- `user_id UUID NOT NULL`
- `role_id UUID NOT NULL`
- `scope_type VARCHAR(24) NOT NULL`
- `scope_id UUID NULL`
- `country_id UUID NULL`
- `vendor_id UUID NULL`
- `valid_from TIMESTAMPTZ NULL`
- `valid_to TIMESTAMPTZ NULL`
- `created_by UUID NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `revoked_at TIMESTAMPTZ NULL`
- `revoked_by UUID NULL`
- `revocation_reason VARCHAR(255) NULL`
- `version INTEGER NOT NULL DEFAULT 1`

Constraints:

- `valid_to` is later than `valid_from` when both exist.
- A grantor cannot assign a role to themselves through the public API.
- Scope columns satisfy the matrix in `architecture.md`.
- Assignment `scope_type` equals the role's required scope type; the service
  enforces this cross-table rule while rows are locked.
- Revocation records actor, time, and reason; rows are not deleted.
- Active overlapping duplicate grants are prevented.

Required indexes:

```text
ix_role_status
ix_permission_status
ix_assignment_user_active
ix_assignment_country_user_active
ix_assignment_vendor_user_active
ix_assignment_role_scope_active
```

Use a PostgreSQL exclusion constraint over normalized validity ranges when the
target PostgreSQL version and migration tooling support it. Otherwise use a
transaction-scoped advisory lock derived from `(user_id, role_id, scope_type,
scope_id)` and query for overlap before insert. A process-local lock is not
acceptable.

## Initial permission catalog

Seed:

```text
iam.role.read
iam.permission.read
iam.assignment.read
iam.assignment.grant
iam.assignment.revoke
iam.session.read
iam.session.revoke
customer.profile.read
customer.profile.write
customer.preference.write
customer.consent.write
customer.address.read
customer.address.write
country.settings.read
country.settings.write
vendor.approve
catalog.product.write
order.cancel
refund.approve
finance.payout.release
```

Permissions for future domains are definitions only. They do not enable an API
or bypass that domain's resource policy.

## Initial roles

Seed immutable system roles:

- `SUPER_ADMIN`, scope `GLOBAL`.
- `COUNTRY_ADMIN`, scope `COUNTRY`.
- `COUNTRY_FINANCE`, scope `COUNTRY`.
- `COUNTRY_OPERATIONS`, scope `COUNTRY`.
- `MODERATOR_SUPPORT`, scope `COUNTRY`.

Do not seed `CUSTOMER`; customer self-service is authorized by authenticated
ownership policies. Vendor roles are added with the vendor domain because
vendor/store scope validation is unavailable in this phase.

The reserved future system-role codes are:

- `VENDOR_OWNER`, scope `VENDOR`.
- `VENDOR_STAFF`, scope `VENDOR`.
- `STORE_STAFF`, scope `STORE`.

They remain role-table records rather than enums. A Nepal vendor assignment does
not authorize UAE operations; UAE selling requires vendor-country approval and
a separately scoped assignment.

The migration gives `SUPER_ADMIN` the complete current permission catalog.
Other role mappings contain only capabilities whose owning APIs exist. A later
migration can add permissions without replacing role IDs.

## Bootstrap policy

The first `SUPER_ADMIN` is created through a one-time operational command that:

1. Requires an existing verified, active account ID.
2. Refuses to run when an active super-admin assignment already exists unless
   an explicit disaster-recovery flag is supplied.
3. Requires recent step-up authentication or offline break-glass approval.
4. Creates the assignment and audit event atomically.
5. Writes no credentials or tokens to terminal output.

Public assignment APIs cannot create, modify, or revoke `SUPER_ADMIN`. That
capability remains an audited break-glass or future maker-checker workflow.

## Authorization data model

Define:

```python
class ResourceScope:
    scope_type: ScopeType
    scope_id: UUID | None
    country_id: UUID | None
    vendor_id: UUID | None

class AuthorizationRequest:
    principal: AuthContext
    permission_code: str
    resource_scope: ResourceScope
    resource_owner_id: UUID | None

class AuthorizationDecision:
    allowed: bool
    reason_code: str
    assignment_id: UUID | None
```

The public caller receives only allowed/denied behavior. Detailed reason codes
are retained in secure diagnostics and metrics, not exposed when they could
reveal protected resources.

## Evaluation algorithm

1. Require an authenticated, active account.
2. Validate that token `auth_version` still equals account `auth_version`.
3. Resolve active assignments at the current UTC time.
4. Resolve active role-permission mappings.
5. Match required permission code.
6. Evaluate assignment scope against the trusted resource scope.
7. Apply ownership or resource-specific policy.
8. Deny when required context is absent, conflicting, expired, or unknown.
9. Record decision metrics without resource PII.

Scope matching:

- A global assignment covers every resource scope for each permission explicitly
  mapped to its role.
- Global destructive operations still require an explicit target country when
  the resource is country-owned.
- A country assignment covers only the exact `country_id`.
- Vendor and store assignments require exact country and ownership ancestry.
- A broader scope never overrides a resource-specific prohibition.
- No role name alone authorizes an operation; the permission mapping is
  authoritative.

## Customer ownership policies

Provide reusable policies:

```python
require_self(principal_user_id: UUID, resource_user_id: UUID) -> None
require_session_owner(principal_user_id: UUID, session_user_id: UUID) -> None
require_address_owner(principal_user_id: UUID, address_user_id: UUID) -> None
```

These fail with a not-found response when revealing resource existence would
leak another user's data.

## FastAPI dependencies

Expose composable dependencies:

```python
get_auth_context() -> AuthContext
require_authenticated() -> AuthContext
require_permission(code: str, scope_resolver: ScopeResolver)
require_recent_step_up(max_age_seconds: int)
```

The scope resolver derives resource scope from trusted route resolution or a
repository lookup. It never accepts a request-body `country_id` as trusted
without resolving it against the target resource.

Route handlers receive an already-authorized command context and call a service.
Services repeat critical ownership/scope checks so background jobs and internal
callers cannot bypass route dependencies.

## Step-up authentication

Sensitive assignment grant/revoke operations require authentication performed
within the previous five minutes using password plus verified OTP, or a future
stronger MFA method.

Phase 2 `STEP_UP` challenges produce a five-minute `iam.auth_grant` bound to:

- User ID.
- Session ID.
- Intended action class.
- Issued and expiry times.

The access token's original `auth_time` alone is insufficient when the policy
requires two methods. The protected command passes the opaque grant token to
`AuthGrantService.consume()` with expected user, session, purpose `STEP_UP`, and
action class. The service locks and consumes the row in the same database
transaction as the assignment grant/revoke, so concurrent replay has one
winner.

## Authorization cache

Cache only resolved permission/assignment projections:

```text
iamz:{user_id}:{auth_version}
```

- Maximum TTL: five minutes.
- Assignment grant/revoke increments the subject account's `auth_version`.
- Cache entries include assignment validity boundaries and expire no later than
  the nearest `valid_to`.
- Cache failure falls back to PostgreSQL; it never grants based on stale data.
- Negative decisions may use a short TTL but are not required.

## Assignment service

`AssignmentService` exposes:

```python
grant_assignment(command: GrantAssignment) -> AssignmentView
revoke_assignment(command: RevokeAssignment) -> AssignmentView
list_assignments(query: AssignmentQuery) -> Page[AssignmentView]
```

Grant flow:

1. Verify `iam.assignment.grant` and consume the grantor's matching step-up
   grant in this transaction.
2. Resolve and lock target account and role.
3. Validate target account and role status.
4. Validate country through `CountryReader`.
5. Verify grantor delegation scope covers the requested role/scope.
6. Prevent self-grant and overlapping active duplicate.
7. Create assignment.
8. Increment target account `auth_version`.
9. Stage the redacted audit event through the transaction-bound `AuditWriter`.
10. Invalidate target authorization cache after commit.

Revoke flow mirrors grant, preserves the row, and requires a reason.

## API contracts

Routes are under `/api/v1/access`.

- `GET /roles` lists active roles the caller may inspect.
- `GET /permissions` lists stable permission definitions.
- `GET /assignments` requires explicit filters and applies caller scope.
- `POST /assignments` grants one role in one scope.
- `DELETE /assignments/{assignment_id}` revokes one assignment with a reason.

Grant request:

```json
{
  "user_id": "uuid",
  "role_code": "COUNTRY_ADMIN",
  "scope_type": "COUNTRY",
  "scope_id": "country-uuid",
  "country_id": "country-uuid",
  "valid_from": null,
  "valid_to": null
}
```

The service derives denormalized columns where possible and rejects conflicting
client values. List endpoints use cursor pagination and never provide an
unscoped “all assignments” query to country administrators.

## Audit actions

Required stable actions:

```text
iam.assignment.granted
iam.assignment.revoked
iam.assignment.grant_denied
iam.authorization.step_up_completed
iam.authorization.step_up_failed
iam.super_admin.bootstrapped
```

Before/after payloads contain role code and scope IDs but no credentials,
tokens, OTPs, email, phone, or address.

## Test-first implementation sequence

1. Add migration/model tests for constraints and indexes.
2. Implement access models and migration.
3. Add deterministic seed tests for permission and role IDs/codes.
4. Implement idempotent seed migration.
5. Add failing scope-matrix and validity-window tests.
6. Implement scope types and policy evaluator.
7. Add failing grant/revoke delegation, duplicate, and audit tests.
8. Implement repository and assignment service.
9. Add failing FastAPI guard and step-up tests.
10. Implement dependencies and access routes.
11. Add cache invalidation and stale-cache tests.
12. Run unit, PostgreSQL integration, and adversarial security tests.

## Required tests

Database:

- Invalid scope-column combinations fail.
- Duplicate role and permission codes fail.
- Duplicate role-permission mapping fails.
- Overlapping active assignment fails.
- Expired and revoked assignments are not active.
- Assignment rows cannot outlive referenced account/role through deletion.

Policies:

- Missing permission denies.
- Correct permission with wrong country denies.
- Correct role name without permission denies.
- Global read can span authorized views.
- Global destructive country operation without explicit country denies.
- Ownership policy hides another user's resource.
- Unknown scope type or missing context denies.

Assignment service:

- Country admin cannot grant outside their country.
- Grantor cannot self-grant.
- Grantor cannot delegate a capability they lack.
- Target `auth_version` increments on grant and revoke.
- State and audit event commit or roll back together.
- Revocation reason is required.
- `SUPER_ADMIN` cannot be managed through public endpoints.

API and security:

- URL/body country tampering returns denied/not found.
- Expired token and stale `auth_version` return unauthenticated.
- Missing/replayed/expired step-up grant denies.
- Assignment list is always caller-scoped.
- Cache outage falls back to current PostgreSQL state.
- A revoked assignment cannot remain usable through cache.

## Completion criteria

Phase 3 is complete when:

- Permission and role seeds are deterministic and migration-safe.
- Every protected test endpoint requires both capability and valid resource
  scope.
- FR-ID-004 step-up tests pass for assignment administration.
- FR-ADM-001 cross-country denial tests pass for queries and commands.
- Every grant/revoke is atomic with a redacted audit event.
- Assignment changes invalidate current access through `auth_version`.
- No public route can manage `SUPER_ADMIN`.

## Handoff to Phase 4

Phase 4 uses:

- `require_authenticated` for all `/me` routes.
- Customer ownership policies for preferences, consents, and addresses.
- `CountryReader` for country/locale/currency/geography validation.
- Audit and optimistic-concurrency conventions established in prior phases.
