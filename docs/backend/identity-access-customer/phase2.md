# Phase 2 — Authentication, OTP, Tokens, and Sessions

## Goal

Deliver secure first-party registration, verification, password recovery,
authentication, token refresh, and device/session management.

This phase satisfies FR-ID-001, FR-ID-002, and FR-ID-003 from the product
specification.

## Prerequisites

- Phase 1 schema, models, repositories, and ports are complete.
- A production `AuditWriter` implementation or transactional audit/outbox
  adapter is available.
- SMTP credentials are held in a secret manager and represented in
  configuration only by secret references.
- A shared Redis deployment is available for production rate limits.

## Technology decisions

Add pinned compatible dependencies through `pyproject.toml` and lock them:

- `argon2-cffi` for Argon2id password hashing.
- `PyJWT[crypto]` for asymmetric JWT signing and validation.
- `email-validator` for email syntax validation.
- `phonenumbers` for E.164 normalization.
- `redis` for distributed rate limiting.

Do not introduce an all-in-one authentication framework. The domain rules,
session rotation, and country-aware policies remain explicit application code.

## Planned file ownership

Create:

```text
app/api/v1/endpoints/auth.py
app/modules/identity/schemas.py
app/modules/identity/passwords.py
app/modules/identity/otp.py
app/modules/identity/grants.py
app/modules/identity/tokens.py
app/modules/identity/rate_limits.py
app/modules/identity/notifications.py
app/modules/identity/ports.py
app/core/auth_context.py
tests/unit/identity/test_passwords.py
tests/unit/identity/test_otp_service.py
tests/unit/identity/test_auth_grant_service.py
tests/unit/identity/test_token_service.py
tests/unit/identity/test_session_service.py
tests/integration/identity/test_auth_endpoints.py
tests/integration/identity/test_refresh_rotation.py
tests/security/test_auth_abuse.py
alembic/versions/<revision>_add_iam_otp_and_sessions.py
```

Modify:

```text
app/modules/identity/models.py
app/modules/identity/repository.py
app/modules/identity/service.py
app/api/v1/router.py
app/core/config.py
pyproject.toml
uv.lock
tests/conftest.py
```

## Database changes

Create `iam.otp_challenge`, `iam.auth_grant`, and `iam.user_session` exactly as
described in `architecture.md`.

Required OTP indexes:

```text
ix_otp_challenge_destination_purpose_created
ix_otp_challenge_user_created
ix_otp_challenge_expires_unverified
```

Required grant indexes:

```text
uq_auth_grant_token_hash
ix_auth_grant_user_purpose_active
ix_auth_grant_session_active
ix_auth_grant_expires_active
```

Required session indexes:

```text
uq_user_session_refresh_token_hash
ix_user_session_user_active
ix_user_session_family
ix_user_session_expires_active
```

The active-session indexes use partial predicates where useful. Session foreign
keys use `ON DELETE RESTRICT`.

The migration also adds any Phase 1 account column omitted from the initial
migration that is required for authentication, but it must not silently alter
existing account semantics.

## Configuration

Add validated settings:

```text
JWT_ISSUER
JWT_AUDIENCE
JWT_PRIVATE_KEY_REFERENCE
JWT_PUBLIC_KEY_SET
JWT_ACTIVE_KEY_ID
ACCESS_TOKEN_TTL_SECONDS=600
REFRESH_TOKEN_TTL_SECONDS=2592000
OTP_TTL_SECONDS=300
OTP_MAX_ATTEMPTS=5
OTP_RESEND_COOLDOWN_SECONDS=60
PASSWORD_RESET_TTL_SECONDS=600
AUTH_DIGEST_PEPPER_REFERENCE
REDIS_URL
SMTP_PROVIDER_REFERENCE
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
```

Production startup fails closed when signing keys, digest pepper, rate-limit
storage, or notification configuration is absent. Secrets are resolved through
an adapter and never printed by settings serialization.

OTP, grant, refresh-token, destination, IP, and user-agent digests are
self-describing strings containing their pepper key ID and algorithm. OTP
challenges additionally store the random `otp_nonce` specified in
`architecture.md`.

## Password policy

- Minimum length: 12 Unicode characters.
- Maximum accepted length: 128 characters to bound hashing work.
- Reject passwords found in a configured breached-password check when that
  adapter is available; adapter failure does not reveal the candidate password.
- Do not require composition rules that encourage predictable substitutions.
- Hash with Argon2id using deployment-benchmarked memory, time, and parallelism.
- Rehash after successful login when stored parameters are outdated.
- Never trim or normalize password contents.

`PasswordHasher` exposes:

```python
hash_password(password: str) -> str
verify_password(password: str, encoded_hash: str) -> PasswordVerification
```

The verification result indicates valid/invalid and whether a rehash is needed.

## OTP design

### Purposes

Support:

- `REGISTER`
- `VERIFY_EMAIL`
- `VERIFY_PHONE`
- `RESET`
- `LOGIN`
- `STEP_UP`

Registration, email verification, password reset, and `STEP_UP` are externally
enabled in this phase. Phone verification is enabled only when the configured
SMS adapter and country policy permit it. Passwordless `LOGIN` remains disabled.

### Generation and storage

- Generate a six-digit code with a cryptographically secure random generator.
- Store a keyed digest using challenge ID, nonce, purpose, and code.
- Store a separate keyed destination fingerprint.
- Persist challenge intent before sending.
- Deliver the raw code only to the notification adapter.
- Notification payloads carry locale and purpose-specific template keys.

### Verification transaction

1. Resolve the challenge by opaque challenge ID.
2. Lock the row.
3. Check purpose, destination fingerprint, expiry, invalidation, previous use,
   and attempt count.
4. Increment attempts before returning an invalid-code result.
5. Set `verified_at` once on success.
6. Apply registration/verification transitions directly, or create a
   single-use purpose-bound `iam.auth_grant` for reset and step-up.
7. Emit an audit/outbox event without OTP or destination data.

## Single-use authorization grants

`AuthGrantService` is the only component that creates or consumes
`iam.auth_grant`.

Creation:

1. Requires a successfully consumed OTP challenge or recent password
   authentication.
2. Generates an opaque token with at least 256 bits of entropy.
3. Stores only a self-describing keyed digest.
4. Binds the grant to user, optional session, purpose, action class, optional
   candidate/resource fingerprint, authentication methods, and correlation ID.
5. Returns the raw token once.

Consumption:

1. Hashes and locates the token.
2. Locks the row.
3. Requires matching user, session, purpose, action class, and subject
   fingerprint when present.
4. Rejects expiry, revocation, and prior consumption.
5. Sets `consumed_at` in the same transaction as the protected state change.

Default lifetimes are ten minutes for `RESET_PASSWORD` and five minutes for
`STEP_UP`. Identifier-change, closure, and export grants are introduced through
the same service in later phases. Password changes, sign-out-all, account
suspension, and closure revoke outstanding grants.

### Rate limits

Use atomic Redis counters with explicit windows:

- OTP request by destination fingerprint.
- OTP request by IP hash.
- OTP request by device ID.
- OTP verification by challenge.
- OTP verification by IP hash.
- Password reset by destination and account.

Every response includes `Retry-After` when limited. Development may use an
in-memory adapter only when one process is guaranteed. Production rejects the
in-memory adapter.

## JWT design

Use an asymmetric algorithm approved by deployment security policy, initially
`RS256` or `EdDSA`. Every JWT includes `kid`; validators accept only configured
algorithms and known keys.

Required claims and checks:

- `iss` equals configured issuer.
- `aud` includes the API audience.
- `sub` parses as an existing UUID account ID.
- `sid` parses as a session UUID.
- `av` equals the account `auth_version`.
- `iat`, `nbf`, and `exp` are valid within a small configured clock skew.
- `jti` is unique.
- `amr` and `auth_time` describe authentication strength and age.

JWT validation does not authorize a country, vendor, or store. It produces an
`AuthContext`, after which Phase 3 evaluates permissions and scope.

## Refresh-token design

Generate at least 256 bits of random entropy and encode with URL-safe base64.
Store only a keyed digest.

Rotation transaction:

1. Locate and lock the token row by digest.
2. Confirm account and session are active and unexpired.
3. Detect prior rotation.
4. Create one replacement row in the same `family_id`.
5. Mark the old row with `rotated_at` and `replaced_by_session_id`.
6. Commit.
7. Return the new raw refresh token and access JWT.

If a rotated token is presented again:

- Revoke all unexpired rows in the family.
- Increment the account `auth_version`.
- Emit `iam.session.refresh_reuse_detected`.
- Return a generic unauthenticated response.
- Notify the account through the security-notification adapter.

Concurrent refreshes allow only one winner. The loser is treated as replay after
the winning transaction commits.

## API contracts

All auth errors use enumeration-safe messages and stable error codes.

### `POST /api/v1/auth/register`

Input:

```json
{
  "email": "buyer@example.com",
  "phone_e164": null,
  "password": "client-only plaintext",
  "country_code": "NP",
  "locale_code": "en-NP"
}
```

Response: `202 Accepted` with an opaque challenge ID and generic delivery
message. Never return whether an active account already exists.

Initial registration requires email and password. Phone is optional. Phone-only
and passwordless registrations return a policy error until a future design
defines their complete verification and recent-authentication lifecycle.

### `POST /api/v1/auth/otp/request`

Accept identifier, purpose, country, and locale. Return `202` for both existing
and unknown identifiers when the purpose is recovery.

### `POST /api/v1/auth/otp/verify`

Accept challenge ID and code. Registration verification activates an eligible
account. Return a purpose-specific result; do not automatically create an
authenticated session unless the request policy explicitly allows it.

### `POST /api/v1/auth/login`

Accept one identifier, password, optional device ID/name, and country context.
Return the access token in JSON. For browser clients, set the refresh token in a
`Secure`, `HttpOnly`, path-restricted cookie. Native clients may use a dedicated
secure-token response profile.

Authentication is global: the account is not restricted to the country used at
registration. The supplied country is resolved for market availability, risk,
and policy messaging, but it is not stored as account ownership and does not
grant country-scoped permissions. The same session may request another active
country; every such request resolves and authorizes that country independently.

### Password recovery

`POST /auth/password/forgot` always returns `202`.

Successful verification of a `RESET` OTP returns a one-time
`RESET_PASSWORD` grant. `POST /auth/password/reset` accepts that opaque grant
plus the new password.
Success:

- Changes the hash.
- Increments `auth_version`.
- Revokes all sessions.
- Consumes the reset grant.
- Emits an audit and security notification.

### Step-up

`POST /auth/step-up/request` requires the current session and successful recent
password verification, then creates a `STEP_UP` OTP challenge.

Successful verification returns a one-time `STEP_UP` grant bound to the current
user, session, and requested action class. Phase 3 consumes the grant
transactionally for the protected action.

### Token and session endpoints

- `POST /auth/token/refresh` rotates the refresh token.
- `POST /auth/logout` revokes the current session and clears the cookie.
- `GET /auth/sessions` returns current user's safe device/session metadata.
- `DELETE /auth/sessions/{session_id}` revokes one owned session.
- `POST /auth/sessions/revoke-all` revokes all sessions except the current one
  only when `keep_current=true`; otherwise it revokes all and clears the cookie.

Session responses never include IP addresses, full user agents, hashes, or
tokens. They may include approximate last activity and whether the row
represents the current session.

## CSRF and cookie rules

When the refresh token uses a cookie:

- Set the cookie path to the common `/api/v1/auth` prefix so both refresh and
  logout endpoints receive it.
- Require an origin check and a synchronizer or double-submit CSRF token for
  mutating cookie-authenticated requests.
- Reject cross-site requests not matching the configured frontend origin.
- Never place access tokens in persistent browser storage as part of this
  backend contract.

## Service boundaries

Add focused services:

- `RegistrationService`
- `OtpService`
- `AuthGrantService`
- `AuthenticationService`
- `PasswordRecoveryService`
- `TokenService`
- `SessionService`

No public service function exceeds 100 lines. Cryptographic primitives are
wrapped, not reimplemented. Services receive clock, random source, repositories,
rate limiter, notifications, audit writer, and transaction manager through
dependencies so tests remain deterministic.

## Test-first implementation sequence

1. Add failing password policy/hash/rehash tests.
2. Implement `PasswordHasher`.
3. Add migration tests for OTP, grant, and session tables.
4. Implement models and repository operations.
5. Add failing OTP generation, expiry, single-use, and attempt-limit tests.
6. Implement OTP service and notification intent.
7. Add failing grant creation, binding, expiry, consumption, and replay tests.
8. Implement `AuthGrantService` and reset/step-up grant issuance.
9. Add failing JWT claim and key-rotation tests.
10. Implement token service and `AuthContext`.
11. Add failing session rotation, concurrency, replay, and revocation tests.
12. Implement session service transactionally.
13. Add registration/login/reset/step-up endpoint tests.
14. Implement thin routes and schemas.
15. Add rate-limit and enumeration security tests.
16. Run the complete unit, integration, and security test sets.

## Required tests

Password:

- Valid hash verifies; incorrect password fails.
- Outdated parameters trigger rehash.
- Length boundaries are enforced.
- Hashes and candidate passwords never appear in logs.

OTP:

- OTP uses secure randomness and is absent from persistence.
- Expired, invalidated, consumed, and over-attempt challenges fail.
- Successful challenge cannot be reused.
- Simultaneous successful verification has one winner.
- Resend invalidates the previous active challenge.
- Destination/IP/device rate limits return `429` with `Retry-After`.
- Recovery responses do not reveal account existence.

Authorization grants:

- Raw grant token is absent from persistence and logs.
- Grant cannot cross user, session, purpose, or action class.
- Concurrent consumption has one winner.
- Expired, revoked, and consumed grants fail.
- Password reset consumes its grant in the password-change transaction.
- Step-up verification returns a session/action-bound grant.

JWT:

- Wrong issuer, audience, algorithm, key, `auth_version`, or expiry fails.
- Unknown `kid` fails closed.
- Suspended and closed accounts fail despite a valid signature.
- Token validation returns no tenant authorization decision.

Sessions:

- Refresh rotates the token and stores no raw value.
- Concurrent refresh allows one replacement.
- Reuse revokes the whole family and increments `auth_version`.
- A user cannot list or revoke another user's session.
- Sign-out-all invalidates earlier access tokens through `auth_version`.
- Expired/revoked sessions cannot refresh.

Endpoints:

- Happy-path register, verify, login, refresh, list sessions, and logout.
- Generic invalid-credential responses.
- Malformed UUIDs and request bodies return stable validation envelopes.
- Cookies have required security attributes.
- CSRF and origin checks protect cookie-authenticated mutations.

## Observability

Emit structured, PII-safe metrics:

- Authentication success/failure by method and country, without identifier.
- OTP requested/sent/verified/expired/limited.
- Refresh success/replay/revocation.
- Active and revoked session counts.
- Notification adapter latency/failure.

Logs include correlation ID and stable event code. They exclude credentials,
tokens, OTPs, destinations, password hashes, and raw IP/user-agent values.

## Completion criteria

Phase 2 is complete when:

- FR-ID-001 through FR-ID-003 have linked passing tests.
- Registration, verification, login, reset, refresh, and session APIs pass
  PostgreSQL integration tests.
- OTP and refresh-token replay tests prove single-use behavior under
  concurrency.
- Production configuration fails closed without key, rate-limit, audit, or
  notification dependencies.
- Raw secrets and PII are absent from database rows, logs, traces, and audit
  payloads.
- Security notifications are generated for password reset, new session,
  sign-out-all, and refresh replay.

## Handoff to Phase 3

Phase 3 consumes `AuthContext` with:

- `user_id`
- `session_id`
- `auth_version`
- `authentication_methods`
- `authenticated_at`
- `correlation_id`

Phase 3 also consumes `AuthGrantService.consume()` for one-time `STEP_UP`
grants. No country/vendor/store permissions are inferred from the JWT or grant.
