# IAM security incident response

Applies to alerts from the IAM security events and metrics: credential
stuffing, OTP spikes, refresh-token replay, cross-country tampering, missing
RLS context, audit/outbox backlog, rotation or retention job failure, and
unexpected runtime database privileges.

Rules for everyone:

- Do not copy emails, phones, addresses, passwords, tokens, or OTPs into the
  incident channel. Use internal user IDs and correlation IDs only, and only in
  channels with restricted access.
- Record every action with a timestamp in the incident record.
- Containment actions that change data need a second approver.

## 1. Triage (first 15 minutes)

1. Acknowledge the alert and name an incident commander.
2. Identify the alert code, country, and time window from the alert payload.
3. Search events by correlation ID and event code (`iam.login.failed`,
   `iam.session.refresh_reuse_detected`, `iam.cross_scope.denied`,
   `iam.authorization.denied`). Events hold hashed signals only; compare hashes,
   never raw values.
4. Decide severity. Treat missing RLS context, unexpected runtime privileges,
   and any refresh replay with confirmed theft as critical.

## 2. Playbooks

### Credential stuffing or password spraying

- Confirm many failures across many identifier hashes from few or many IP
  hashes.
- Tighten rate limits for the affected country. Do not disable them.
- Keep recovery responses enumeration-safe; do not add account-specific
  messages.
- For accounts with a confirmed successful login after failures, revoke their
  sessions and require password reset via the recovery runbook.

### OTP request or verify spike

- Check `iam.otp.rate_limited` counts by country and channel.
- Confirm the SMTP provider is healthy and not producing retries.
- Keep limits on. If a provider outage drives retries, preserve challenge
  intent and let controlled retry run; never expose codes.

### Refresh replay

- Replay revokes the session family automatically. Confirm the revocation event
  exists for each detection.
- Above baseline or across many users: suspect token or pepper exposure. Start
  key review in `iam-key-rotation.md` and revoke all session families issued in
  the window.

### Cross-country tampering

- Repeated `iam.cross_scope.denied` from one actor hash: confirm denials were
  correct and no data was returned.
- Review the actor's assignments. Revoke the assignment if abuse is confirmed,
  with a second approver.

### RLS context missing or unexpected runtime privileges

- Critical. Stop the rollout of the change that introduced it.
- Compare the runtime role grants with the approved least-privilege manifest.
- Do not restore access by widening grants. Rollback is disabling the affected
  policy through the migration role with an incident and change record, while
  application authorization stays active.

### Audit or outbox backlog

- Sensitive state changes stay persisted and queued. Check the consumer and the
  database for blocking transactions.
- Clear the backlog before any rollout continues. Record the lag peak.

### Rotation or retention job failure

- Retention: a missing policy means data is retained and the alert fires. Do
  not run retention without an approved policy version and a change ticket.
- Rotation: see `iam-key-rotation.md` failure handling.

## 3. Containment commands

- Revoke one user's sessions through the support tooling for logout-all.
- Revoke a session family for confirmed replay.
- Rotate the digest pepper or JWT key only through `iam-key-rotation.md`.

## 4. Recovery and closure

1. Confirm alerts return to baseline for a full evaluation window.
2. Notify affected users through the standard security notification, with no
   identifiers in logs.
3. Notify the privacy lead if personal data may have been exposed. Country
   legal owners decide on regulator and customer notices and their deadlines.
4. Hold a post-incident review within five working days. File follow-up items
   for tests, thresholds, and runbook gaps.

## Evidence to keep

Timeline, alert payloads, event correlation IDs, approvals, commands run,
metric screenshots, and the review notes. Store them in the incident record.
