# IAM key and pepper rotation

Scope: JWT signing/verification keys and the digest pepper used for OTP,
auth-grant, refresh-token, destination, IP, and user-agent digests.

Never paste private keys, peppers, or tokens into tickets, chat, shell history,
or logs. All planning commands below handle key IDs and timestamps only.

## Roles and prerequisites

- Change owner: runs the steps. Reviewer: approves each stage in the ticket.
- An approved change ticket exists. A staging rehearsal is complete.
- Access to the secret manager and the deployment pipeline. Nobody needs to
  read secret values; the secret manager generates and stores them.
- Current values known: `ACCESS_TOKEN_TTL_SECONDS`, `REFRESH_TOKEN_TTL_SECONDS`,
  published key IDs, active key ID, configured pepper versions.

## Planning command

```text
uv run python scripts/rotate_iam_keys.py <command> [options]
```

Exit code `0` means the step is allowed, `2` means a safety gate refused it,
`3` means bad input. Output is JSON containing steps, the earliest allowed
time, readable pepper versions, and an audit record. Attach it to the ticket.
Use `--now` only for rehearsals.

## JWT key rotation

1. Add the verification key.
   - `jwt-add --kid NEW --public-kids OLD`
   - Generate the new key pair in the secret manager. Publish only the public
     key in `JWT_PUBLIC_KEY_SET` next to the old key. Keep `JWT_ACTIVE_KEY_ID`
     unchanged.
   - Deploy to every verifier. Wait until the rollout is complete everywhere.
2. Activate signing.
   - `jwt-activate --kid NEW --public-kids OLD,NEW --active-kid OLD`
   - The command refuses a key that is not already published. Set
     `JWT_ACTIVE_KEY_ID` and the private-key reference to the new key.
   - Remove signing access to the old private key immediately. Note the cutover
     time (UTC).
3. Verify mixed-key traffic.
   - New logins issue tokens with the new `kid`. Tokens signed with the old
     `kid` still validate until they expire.
   - Check dashboards: login success rate, 401 rate, unknown-`kid` failures.
4. Remove the old verification key.
   - `jwt-remove --kid OLD --public-kids OLD,NEW --active-kid NEW --cutover-at <UTC time>`
   - The command refuses until cutover time plus `ACCESS_TOKEN_TTL_SECONDS`
     plus clock skew (default 30 seconds) has passed, and it prints the earliest
     allowed time.
   - Remove the old public key from `JWT_PUBLIC_KEY_SET` and deploy.
5. Record each stage with the printed audit record and a timestamp.

Rollback: before step 2, remove the new public key. After step 2, reactivate
the old key only if its private key was not destroyed and was not compromised.
If compromise is suspected, follow `iam-security-incident.md`.

## Digest pepper rotation

Digests carry their pepper version, so old values stay readable while the old
version is configured.

1. Check state.
   - `pepper-status --active-pepper V2 --configured-peppers V1,V2`
   - Confirm the readable versions match what you expect.
2. Add the new version in the secret manager and configure it as the only active
   version for new writes. Keep the previous version configured. Deploy.
3. Refresh tokens rotate into the new version after successful use. OTP and
   auth grants expire quickly.
4. Retire the old version.
   - `pepper-retire --active-pepper V2 --configured-peppers V1,V2 --retire V1 --active-since <UTC time>`
   - The command refuses until `REFRESH_TOKEN_TTL_SECONDS` has passed since the
     new version became active (override with `--dependent-ttl-seconds` only
     with reviewer approval). Remove the old version and deploy.
5. Expect users whose refresh tokens still used the retired version to sign in
   again. This is documented behavior, not an incident.

Suspected pepper compromise: do not wait for expiry. Follow the session-family
revocation steps in `iam-security-incident.md`, then rotate.

## Failure handling

- A rotation failure raises the rotation/retention failure alert. Stop, keep
  the previous configuration, and open an incident if traffic is affected.
- Unknown-`kid` failures after step 1 mean a verifier missed the rollout.
  Finish the rollout before continuing.

## Evidence for go-live

Attach to the release package: rehearsal ticket, each plan output, the
mixed-key validation test result, and the time the old key was removed.
