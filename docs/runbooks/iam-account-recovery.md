# IAM account recovery

Covers customers who cannot sign in, suspected account takeover, identifier
changes, and closed accounts. Support never sees passwords, OTPs, tokens, or
session hashes, and never asks customers to share them.

## Principles

- Recovery is self-service first. Responses must not reveal whether an email or
  phone has an account.
- Support cannot read or set a password or an OTP.
- Closed accounts are not reactivated through login or password reset.
- Do not paste customer identifiers into shared channels. Use the internal user
  ID in restricted tools.

## 1. Customer cannot sign in

1. Ask the customer to use the password-reset flow. The response is identical
   whether or not the account exists.
2. If the one-time code does not arrive, check the provider status and the
   `iam.otp.rate_limited` metric for the country. The customer waits for the
   resend cooldown. Support does not bypass limits.
3. If the code still fails, the customer retries after the cooldown. Multiple
   failures lock the challenge, and the customer starts a new one.

## 2. Suspected account takeover

1. Confirm the report through an authenticated channel, not by inbound email
   content alone.
2. In restricted tooling, revoke all sessions for the user (logout-all). This
   also increments the account auth version so access tokens stop working.
3. Have the customer reset the password through the standard flow, which
   revokes other sessions again.
4. Review `iam.login.*` and `iam.session.*` events by user ID and correlation
   ID. Compare hashed signals only.
5. Escalate to `iam-security-incident.md` if more than one account is
   affected or replay detections appear.

## 3. Lost access to the registered email or phone

1. The customer signs in with the password and starts the identifier change
   flow, which requires recent authentication and verification of the new
   destination. The previous destination is notified.
2. If the customer cannot sign in and cannot receive codes, support does not
   change identifiers on request. Escalate to the security administrator, who
   applies the documented identity-verification policy with a second approver
   and records audit evidence.

## 4. Closed accounts

- Closure sets the status to closed, removes password usability, revokes
  sessions, OTPs, and grants, replaces email and phone with non-routable
  tombstones, and pseudonymizes eligible addresses.
- Support cannot reverse a closure. Reversal needs a documented legal policy
  and a maker-checker workflow approved by the privacy lead.
- Whether a previous email or phone may register again is a country privacy and
  fraud policy decision. Direct the customer to create a new account only if
  the country policy allows it.

## 5. Data export requests

1. The customer starts the export in the app: password check, email one-time
   code, then an export request for one country.
2. Exports are delivered through a short-lived download grant. Support never
   sends the archive by email or chat.
3. A failed or expired export is requested again by the customer.

## 6. Escalation and records

- Record each recovery action with the user ID, time, operator, and approver.
- Escalate to the security on-call for confirmed takeover with data access, and
  to the privacy lead for suspected personal-data exposure.
