# 06 — Events: sign-up forms, attendee export, QR check-in

**Depends on:** 02 (resumable bulk-send/claim machinery and `emailing.py` conventions),
05 (events routes/models it builds on).

## Goal

Per-event custom questions on booking, a CSV export with answers, a QR ticket in the
confirmation email and "my bookings", and a mobile door check-in screen for committee.

## Owns

- migrations `043`–`046` (questions, answers, attendance, check-in tokens — expand-only)
- `backend/api/routes/checkin.py` (new), signup-question endpoints wherever they fit
  cleanly into the existing events routes (05 has landed; you may edit
  `backend/api/routes/events.py` now)
- `backend/database/routes/events.py` + `models/events.py` (question/answer/attendance
  queries)
- `backend/api/services/emailing.py` — only the booking-confirmation additions (02's
  resumable-send design has landed; keep it intact; transactional emails stay
  automatic)
- `frontend/src/views/EventView.vue` (booking form with custom questions),
  `frontend/src/views/PortalView.vue` ("my bookings" QR ticket),
  `frontend/src/views/CheckinView.vue` (new), `frontend/src/router/index.js`,
  `frontend/src/api/index.js`
- `backend/api/utils/deps.py`/`roles.py` only if a checkin capability check needs it
  (check-in and answers require the `events` capability — prefer existing capability
  reuse over new roles)
- `API.md`, `deploy/snapshot/scrub.sql` (answers hold personal data!)
- tests under `tests/checkin/` and `tests/e2e/checkin/`

## Design notes

- **QR tokens must be unguessable signed values** (HMAC, like the existing
  `EMAIL_TOKEN_SECRET` signing in `services/emailing.py` — reuse the pattern and the
  secret), not booking ids. The QR encodes a URL the check-in screen can consume by
  scan, and the token is bound to the booking.
- Guests book too: the QR and check-in must work for guest bookings as for members.
- Answers are personal data: visible only to holders of the `events` capability
  (endpoint-level check, not just UI hiding), and **deleted automatically 30 days after
  the event** (configurable via an env/config setting with a sane default). The purge
  rides the same background-job mechanism 02 made restart-safe.
- CSV export: attendees with answers, correct content-disposition, streamed or paginated
  enough not to block the event loop on big events.
- The check-in screen: mobile-first, camera scan (a browser-side QR library — keep it
  small and CSP-friendly) **and** name search fallback, mark attended, live attended
  count. Committee-only route.

## Acceptance criteria

1. An admin defines questions (text, single-choice, multi-choice; required/optional);
   a member books and must answer required ones (API rejects missing answers), optional
   ones persist; choices round-trip.
2. Guest booking with answers works identically.
3. CSV export contains one row per attendee with answers; a member without the `events`
   capability cannot fetch it (403) and the questions' answers never appear in any
   public/member-facing endpoint (assert on payloads).
4. The booking confirmation email (mailpit) contains the QR (an image or a link that
   renders one) and "my bookings" shows it; the QR encodes a signed token, and a forged
   token (tampered payload, wrong signature) is rejected with 4xx.
5. Check-in flow: scan/submit a valid token → attendee marked attended, live count
   increments; search-by-name check-in works for members and guests; double check-in is
   idempotent or clearly reported, not silent.
6. Answers are purged by the retention job: a test fast-forwards (or injects a past
   event date) and asserts answers are gone while bookings survive; the retention window
   is configurable.
7. `verify.sql`'s guarantee holds: every new personal-data column (answers, tokens if
   identifying, guest fields) is scrubbed — staging snapshot test or direct scrub run
   stays clean.
8. Every new send path (QR confirmation) goes through the existing MAIL_ALLOWLIST /
   maintenance guards — test that staging-style allowlist settings block it.
9. `API.md` updated; existing booking/holds/waitlist tests still green.

## Constraints

- Transactional emails (confirmations) stay automatic; no queue involvement here.
- Migrations expand-only; no secrets in repo; comments explain why.
