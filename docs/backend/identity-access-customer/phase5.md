# Phase 5 — Isolation, Privacy, and Production Hardening

## Goal

Harden the completed identity/access/customer subsystem with selective RLS,
transaction-local tenant context, account closure and pseudonymization,
security observability, adversarial tests, and production rollout gates.

This phase closes AC-11 and the privacy/security requirements in sections 23
and 24 of the product specification.

## Prerequisites

- Phases 1 through 4 are complete.
- Application-level authorization and ownership tests pass before RLS is added.
- Separate PostgreSQL runtime and migration roles are available.
- Production secret manager, Redis, SMTP, audit, metrics, and alerting
  integrations are available in a staging environment.
- Country legal owners have supplied retention and erasure rules for launch
  markets.

## Deliverables

- Selective RLS for high-risk IAM/customer tables.
- Transaction-local database request context compatible with connection pools.
- Account closure, pseudonymization, data access/export, and retention jobs.
- Security event metrics, dashboards, and alerts.
- Key/pepper rotation runbooks.
- Adversarial authorization and connection-leak tests.
- Requirement traceability and go-live evidence package.

## Planned file ownership

Create:

```text
app/core/database_context.py
app/modules/identity/privacy.py
app/modules/identity/security_events.py
app/modules/customers/privacy.py
app/modules/customers/exports.py
app/modules/customers/retention.py
scripts/rotate_iam_keys.py
scripts/process_iam_retention.py
tests/integration/test_database_context.py
tests/integration/test_iam_rls.py
tests/integration/test_account_closure.py
tests/integration/test_customer_export.py
tests/security/test_tenant_tampering.py
tests/security/test_pool_context_leakage.py
tests/security/test_sensitive_data_leakage.py
tests/security/test_token_key_rotation.py
docs/runbooks/iam-key-rotation.md
docs/runbooks/iam-security-incident.md
docs/runbooks/iam-account-recovery.md
alembic/versions/<revision>_add_iam_data_export_requests.py
alembic/versions/<revision>_enable_iam_rls.py
```

Modify:

```text
app/core/database.py
app/core/dependencies.py
app/core/config.py
app/modules/identity/repository.py
app/modules/identity/service.py
app/modules/access/repository.py
app/modules/access/service.py
app/modules/customers/repository.py
app/modules/customers/service.py
tests/conftest.py
```

## PostgreSQL roles

Use distinct credentials:

- `sukilo_migrator`: owns schemas/migrations and may bypass RLS.
- `sukilo_runtime`: no superuser, schema ownership, or `BYPASSRLS`; used by the
  FastAPI service.
- `sukilo_iam_jobs`: no superuser, schema ownership, or `BYPASSRLS`; used only
  by export and retention workers through job-specific policies.
- `sukilo_readonly_ops`: controlled operational read access through approved
  views, not raw PII tables.

The runtime role receives only required table/sequence/function grants. It
cannot disable policies, create extensions, alter tables, or assume the
migration role.

## Transaction-local database context

After API authentication and authorization, set these values in the same
database transaction used by repositories:

```text
app.actor_user_id
app.effective_user_id
app.effective_country_id
app.authorization_mode
app.action_class
app.job_purpose
app.correlation_id
```

Allowed authorization modes:

- `SELF`
- `COUNTRY_OPERATOR`
- `GLOBAL_OPERATOR`
- `SYSTEM_JOB`
- `ACCOUNT_CLOSURE`

Rules:

- Use `SET LOCAL` or `set_config(..., true)`.
- Begin the transaction before setting context.
- Do not set custom values at connection/session scope.
- Reject an operation if required context is absent.
- Clear behavior is verified by acquiring the same pooled connection in a
  subsequent transaction.
- Repository APIs continue requiring explicit IDs; RLS is not the primary
  authorization mechanism.
- Background jobs use narrow job-specific identities and explicit target
  country, never an unrestricted runtime mode.

The database-context dependency accepts only an already-authorized command
context. Request-body values cannot set database authorization mode.

## RLS policy set

Enable and force RLS for `sukilo_runtime` and `sukilo_iam_jobs` on:

- `iam.user_role_assignment`
- `iam.consent_record`
- `iam.address`
- `iam.data_export_request`

Evaluate `iam.user_preference` after production threat review. It may use RLS
if all call paths consistently establish context; otherwise application
ownership checks remain mandatory and the table is added in a follow-up
migration.

### Assignment policy

Allow:

- `SELF` to read only assignments whose `user_id` equals effective user.
- `COUNTRY_OPERATOR` to read/manage rows whose `country_id` equals effective
  country, subject to prior application permission checks.
- `GLOBAL_OPERATOR` only with explicit effective country for country-scoped
  rows.
- Controlled global assignment reads with an explicit separate operation.
- `SYSTEM_JOB` has no assignment-table access.

Deny public/runtime insertion of `SUPER_ADMIN` through an RLS-compatible
database trigger or service constraint. Break-glass tooling uses the migration
or dedicated security-administration path and writes audit evidence.

### Consent policy

Allow:

- `SELF` to read/insert own consent records.
- `COUNTRY_OPERATOR` to read records only in effective country after application
  permission checks.
- `SYSTEM_JOB` under database role `sukilo_iam_jobs` to read one effective
  user's records in one effective country only when `job_purpose=CUSTOMER_EXPORT`.
- No update or delete under runtime role.

An insert check requires both row `user_id` and `country_id` to equal the
trusted context for self-service.

### Address policy

Allow:

- `SELF` to read/insert/update owned active rows only when both `user_id` and
  `country_id` equal the effective database context.
- `SYSTEM_JOB` under `sukilo_iam_jobs` to read active and deleted rows for one
  effective user/country when `job_purpose=CUSTOMER_EXPORT`.
- `SYSTEM_JOB` under `sukilo_iam_jobs` to update policy-approved rows in one
  effective country when `job_purpose=RETENTION`; application batch selection
  and retention holds remain mandatory.
- `ACCOUNT_CLOSURE` under `sukilo_runtime` to read/update active and deleted rows
  whose `user_id` equals effective user, across that user's countries, only
  after the service consumes a `CLOSE_ACCOUNT` grant in the same transaction.
- Controlled future fulfillment access through a separately reviewed policy,
  not `GLOBAL_OPERATOR`.

Soft delete is an update and must preserve owner and country. No runtime hard
delete is allowed.

### Data-export request policy

Allow:

- `SELF` to insert/read an owned request in the effective country and to mark a
  ready owned request downloaded only through the download service.
- `SYSTEM_JOB` under `sukilo_iam_jobs` to claim and update one request when
  `job_purpose=CUSTOMER_EXPORT` and effective user/country match the row.
- No hard delete under runtime or job roles.

### Policy safety

- Missing or malformed `current_setting` values resolve to deny, not SQL errors
  that disclose row existence.
- Migration tests inspect policy definitions and role grants.
- `SECURITY DEFINER` functions are avoided unless required; if used, they set a
  fixed safe `search_path`, validate caller role, and have restricted execute
  grants.

## Account closure lifecycle

### Closure request

`POST /api/v1/me/closure`:

1. Requires recent password authentication and consumes a `STEP_UP` auth grant
   bound to action class `ACCOUNT_CLOSURE`.
2. Returns a preview of effects and country-specific retention notices.
3. Creates a single-use `CLOSE_ACCOUNT` auth grant valid for 15 minutes.
4. Does not close the account before confirmation.

`POST /api/v1/me/closure/confirm`:

1. Locks the account, consumes the `CLOSE_ACCOUNT` auth grant, and sets
   transaction-local `ACCOUNT_CLOSURE` context for that effective user.
2. Rejects pending legal/finance holds reported by a retention-policy port.
3. Sets status `CLOSED` and `closed_at`.
4. Removes password usability and increments `auth_version`.
5. Revokes every session, OTP challenge, and outstanding auth grant.
6. Removes external auth identity links where retention is unnecessary.
7. Replaces eligible email/phone with deterministic non-routable tombstone
   values containing no original identifier.
8. Soft-deletes and pseudonymizes eligible customer addresses.
9. Records consent/privacy action evidence required by policy.
10. Writes audit/outbox events in the same transaction.
11. Sends confirmation to the previous destination through a pre-transaction
    notification intent that contains no identifier in logs.

Closure does not delete order, payment, ledger, tax, dispute, fraud, or audit
records owned by future domains. Those domains retain stable `user_id` and
apply their own legal pseudonymization rules.

### Reuse and reactivation

- Closed accounts are not reactivated through login or password reset.
- Whether a previous email/phone can register again is a country privacy/fraud
  policy decision.
- A keyed abuse-prevention tombstone may be retained separately when lawful;
  it cannot reconstruct the original identifier.
- Support cannot reverse closure without a documented legal policy and
  maker-checker workflow.

## Data access and export

### `iam.data_export_request`

Columns:

- `export_request_id UUID PRIMARY KEY`
- `user_id UUID NOT NULL REFERENCES iam.user_account`
- `country_id UUID NOT NULL`
- `idempotency_key VARCHAR(128) NOT NULL`
- `status VARCHAR(24) NOT NULL`
- `artifact_reference TEXT NULL`
- `artifact_expires_at TIMESTAMPTZ NULL`
- `requested_at TIMESTAMPTZ NOT NULL`
- `started_at TIMESTAMPTZ NULL`
- `completed_at TIMESTAMPTZ NULL`
- `failed_at TIMESTAMPTZ NULL`
- `failure_code VARCHAR(64) NULL`
- `downloaded_at TIMESTAMPTZ NULL`
- `correlation_id UUID NOT NULL`
- `version INTEGER NOT NULL DEFAULT 1`

Statuses are `PENDING`, `PROCESSING`, `READY`, `FAILED`, `EXPIRED`, and
`DOWNLOADED`. Unique `(user_id, country_id, idempotency_key)` makes request
retries safe. `artifact_reference` is a secret-manager/object-store reference,
not a public URL. The migration adds user/status and expiry indexes and RLS
ownership policies.

`POST /api/v1/me/data-export/verification` requires the current password and
creates an OTP challenge. Successful OTP verification issues a one-time
`DATA_EXPORT` auth grant bound to the current user/session.

`POST /api/v1/me/data-export` consumes that grant and creates an asynchronous
export request for one explicit country in the same transaction. It requires an
idempotency key and returns `export_request_id`.

`GET /api/v1/me/data-exports/{export_request_id}` returns owner-scoped status.

`POST /api/v1/me/data-exports/{export_request_id}/download-grant` requires a
`READY` owned request and recent authentication. It locks the export-request
row, rechecks artifact expiry, revokes any previous unconsumed download grant
for that request, and creates one five-minute `DOWNLOAD_EXPORT` auth grant whose
subject fingerprint binds the request ID. Concurrent issuance is serialized by
the request-row lock.

`POST /api/v1/me/data-exports/{export_request_id}/download` consumes that grant,
locks the request, requires status still equals `READY` and artifact is
unexpired, atomically sets `downloaded_at`/`DOWNLOADED`, and returns a streamed
encrypted artifact or a very short-lived one-time object-store URL. Concurrent
downloads have one winner.

The export contains:

- Account projection.
- Preferences.
- Consent history.
- Active and deleted address history allowed by policy.
- Session/security history suitable for the user.
- Links or references supplied by future orders, reviews, and support domains.

Rules:

- Generate under `sukilo_iam_jobs` with `SYSTEM_JOB`,
  `job_purpose=CUSTOMER_EXPORT`, and explicit user/country context copied from
  the request row.
- Encrypt the artifact and store it with short expiry.
- Consume a request-bound one-time download grant; never email the raw archive.
- Audit request, generation, download, expiry, and failure.
- Exclude password hashes, token/session hashes, OTPs, internal fraud signals,
  role-administration internals, third-party PII, and secret references.

## Retention processing

`RetentionPolicyReader` returns effective policy by country and data class.

The scheduled job:

1. Selects one country and data class explicitly.
2. Uses bounded batches and stable cursors.
3. Skips legal/finance/fraud holds.
4. Produces a dry-run count before destructive execution.
5. Pseudonymizes or deletes only policy-approved fields/rows.
6. Records aggregate operational evidence and per-resource audit references.
7. Is idempotent and resumable.

Production execution requires an approved policy version and change ticket. A
missing policy means retain and alert, not delete.

## Cryptographic rotation

### JWT keys

- Publish multiple verification keys with stable `kid` values.
- Add a new signing key before making it active.
- Continue verification of the old key until all issued access tokens expire
  plus maximum clock skew.
- Remove old private key access immediately after signing cutover.
- Audit each stage and test mixed-key validation.

### Digest pepper

Version OTP, auth-grant, refresh-token, destination, IP, and user-agent digests
with a pepper key ID encoded in each self-describing digest string.

- New writes use the active pepper.
- Verification tries only explicitly configured active/previous versions.
- Refresh tokens rotate into the current version after successful use.
- Retire old pepper only after all dependent values expire or are migrated.
- A suspected pepper compromise triggers session-family revocation according to
  the incident runbook.

## Security event model

Emit structured events:

```text
iam.login.succeeded
iam.login.failed
iam.otp.rate_limited
iam.password.changed
iam.session.created
iam.session.revoked
iam.session.refresh_reuse_detected
iam.assignment.granted
iam.assignment.revoked
iam.authorization.denied
iam.cross_scope.denied
iam.account.closure_requested
iam.account.closed
iam.data_export.requested
iam.data_export.downloaded
```

Every event includes timestamp, correlation ID, event code, result, country when
known, and hashed security signals. It excludes raw identifiers, addresses,
credentials, tokens, OTPs, and request bodies.

## Metrics and alerts

Metrics:

- Login and OTP outcomes by country/channel.
- Rate-limit activation.
- Refresh replay detections.
- Session revocation volume.
- Authorization denials by permission and scope type.
- Cross-country denial count.
- RLS denial count.
- Closure/export/retention job outcomes.
- Audit/outbox delivery lag.

Alerts:

- Credential-stuffing pattern.
- OTP request/verify spike.
- Any refresh replay above normal baseline.
- Repeated cross-country tampering.
- RLS context missing in authenticated operations.
- Audit/outbox backlog beyond agreed threshold.
- Key rotation or retention job failure.
- Runtime database role gaining unexpected privileges.

Alert payloads identify internal resource IDs only when access to the alert
channel is restricted.

## Adversarial test matrix

### Authentication

- Credential stuffing and password spraying encounter distributed limits.
- Account recovery remains enumeration-safe.
- Expired, wrong-purpose, and replayed OTPs fail.
- JWT algorithm confusion, unknown `kid`, issuer/audience mismatch, and stale
  `auth_version` fail.
- Refresh replay revokes the family under concurrent requests.

### Authorization

- Customer A cannot access Customer B sessions, preferences, consents, or
  addresses by changing URL/body IDs.
- Country admin NP cannot query or mutate AE/VN/KH assignments.
- A forged JWT country claim does not expand server-resolved scope.
- Super admin destructive operation without explicit country fails.
- Revoked/expired assignments fail immediately after cache invalidation.
- Step-up grants cannot cross user, session, or action class.

### Database isolation

- Direct runtime SQL without request context returns no protected rows.
- Self context cannot insert another owner/country.
- Country context cannot read another country.
- Closure context can update only the effective user's active/deleted addresses
  and cannot be reused after its transaction.
- Transaction rollback clears local settings.
- Reused pooled connection starts with no previous context.
- Migration role can apply migrations; runtime role cannot alter policies.

### Privacy

- Logs, traces, metrics, errors, audit records, and exports are scanned for
  credential, token, OTP, email, phone, and address patterns.
- Closure leaves required stable references but removes eligible contact/address
  PII.
- Export includes only the requesting user and excludes security secrets.
- Concurrent download-grant issuance leaves one active grant; concurrent
  downloads have one winner and one `DOWNLOADED` transition.
- Retention dry-run and execution are idempotent.

## Performance and resilience tests

- Measure password hashing under target concurrency; tune without exceeding API
  latency/resource budgets.
- Load OTP request and verify rate-limit paths.
- Load login and token refresh with Redis and notification failures.
- Test authorization cache outage; PostgreSQL fallback remains correct.
- Test audit/outbox delay; sensitive state change remains persisted and queued.
- Test RLS query plans use user/country indexes and avoid full scans.
- Test export and retention jobs in bounded batches without request starvation.

Security controls fail closed, but provider notification outages preserve
challenge intent for controlled retry and never expose codes.

## Rollout sequence

1. Deploy schema and runtime-role grants with RLS policies disabled.
2. Run application ownership and scope suites in staging.
3. Enable policies in observe/test transactions and compare expected row sets.
4. Enable and force RLS per table in staging.
5. Run pooled-connection leakage and adversarial suites.
6. Deploy production code capable of setting transaction-local context.
7. Enable production RLS one table at a time: consent, address, data-export
   request, assignment.
8. Monitor denials, errors, query latency, and audit lag.
9. Complete key rotation rehearsal and account closure/export exercises.
10. Approve country launch only after all gates have evidence.

Rollback disables the affected policy through the migration role while
application authorization remains active. Rollback is audited and requires an
incident/change record.

## Requirement-to-evidence matrix

- FR-ID-001: registration/email OTP integration and abuse tests from Phase 2.
- FR-ID-002: distributed request/attempt rate-limit tests from Phase 2 and load
  tests in this phase.
- FR-ID-003: list/revoke/logout-all ownership tests from Phase 2.
- FR-ID-004: step-up binding and replay tests from Phase 3.
- FR-CUS-005: country validation, ownership, and default-address tests from
  Phase 4.
- FR-ADM-001: application and RLS cross-country tests from Phases 3 and 5.
- AC-11: URL, body, token-claim, repository, and direct-SQL tampering tests.
- Privacy requirements: closure, export, retention, and leakage scan evidence.

## Production gates

Security gate:

- Threat model reviewed.
- Penetration test has no unresolved critical/high findings.
- JWT/pepper rotation rehearsal passes.
- Runtime role privilege diff is approved.
- Secrets and PII leakage scan passes.

Functional gate:

- All phase completion criteria have linked test runs.
- NP, AE, VN, and KH country-context isolation scenarios pass.
- Account recovery, closure, and export exercises pass.
- Admin scope and explicit-country destructive-action tests pass.

Operational gate:

- Dashboards and alerts are active.
- Incident, key rotation, recovery, and retention runbooks are exercised.
- Audit/outbox lag is within the agreed threshold.
- Backup restore includes `iam` schema and RLS policies.
- On-call owners and escalation contacts are assigned.

Legal/privacy gate:

- Effective terms/privacy policy versions are configured by country.
- Retention, legal-hold, export, closure, and identifier-reuse rules are
  approved for the launching country.
- Marketing consent remains separate from transactional communication.

## Completion criteria

Phase 5 is complete when:

- Selected IAM tables enforce tested RLS without pooled-context leakage.
- Runtime database privileges match the least-privilege manifest.
- AC-11 passes at API, service, repository, and database layers.
- Closure, export, and retention behavior has country-policy evidence.
- Key and pepper rotation rehearsals complete without invalidating legitimate
  active traffic outside documented expiry behavior.
- Security metrics, alerts, and runbooks are exercised in staging.
- Every requirement in the evidence matrix links to a current passing test or
  approved operational/legal artifact.

## Downstream readiness

Vendor, catalog, pricing, commerce, and finance modules may depend on:

- Stable `user_id`.
- Validated `AuthContext` and `CountryContext`.
- Permission checks with explicit resource scope.
- Account/session security lifecycle.
- Customer address selection followed by immutable domain-owned snapshots.
- Audit/outbox contracts.

They may not bypass these interfaces by reading token role names, trusting
client country IDs, or querying IAM tables directly.
