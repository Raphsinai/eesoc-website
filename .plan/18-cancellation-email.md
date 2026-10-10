# 18 — Email the holder when a booking is cancelled

**Goal:** every booking that becomes `cancelled` gets exactly one "your booking was
cancelled" email, sent durably (claimed, retried, recorded) like the confirmation email.

Request: `requests/08-cancellation-email.md`. Depends on: 16 merged (it reshapes
`utils/email.py` and the confirmation sender this mirrors). Runs in parallel with 07.

## Owns

- `backend/database/migrations/067_booking_cancellation_emails.sql` (new, expand-only)
- `backend/database/routes/cancellation_notices.py` (new adapter router: claim + result
  routes) and its one registration line in `backend/database/main.py`;
  `backend/database/models/cancellation_notices.py` (new) if a model is needed
- `backend/api/services/cancellations.py` (new — the sender)
- `backend/api/utils/email.py` — a new `send_booking_cancelled` only; do not touch
  `send_booking_confirmed` or other senders
- `backend/api/services/announce.py` — the one line that runs the sender on the
  announcer's ten-minute loop, next to `confirmations.send_pending()`
- `tests/cancellation-email/` (pytest)

**Must not touch:** `backend/database/routes/events.py`, `backend/api/routes/events.py`,
`backend/api/routes/admin.py` (owned by sections running in parallel or already merged
with their own owners). Design below needs none of them.

## Design

Mirror `services/confirmations.py` (read it first — module docstring, `_lock`,
`send_pending`, claim with SKIP LOCKED, outcome recording, `mailer.configured()` /
`mailer.deliverable()` guards, retry budget):

1. **Migration 067:** a per-booking cancellation-email state. Either columns on
   `reservations` (`cancellation_email` with the same value set as
   `confirmation_email` — sending/sent/failed/skipped/suppressed — plus
   `cancellation_sent_at`, `cancellation_attempts`) or a new table keyed by
   `reservation_id`; pick the one that matches 059's conventions and say why. It must
   **mark every already-cancelled booking `skipped`** (no backfill emails — the 059
   precedent). No personal data in the new columns/table (if it stores an error string,
   make sure it can't contain an address; otherwise add it to `deploy/snapshot/scrub.sql`
   — the snapshot verify job fails on survivors).
2. **Pending = ** `status = 'cancelled'` and no cancellation-email state yet. No change to
   the cancel UPDATE is needed: the sender finds new cancellations by that predicate. The
   claim query uses an index-friendly predicate (partial index if needed) so the
   ten-minute pass stays cheap.
3. **Skip, don't send** (record `skipped`): a committee-added ticket whose confirmation
   email was deliberately skipped (the committee unticked "email them") — the holder
   never heard of it; a booking for an event that has already ended.
4. **Address:** the same resolution the confirmation claim uses (account email, or the
   guest's email for a guest booking). Undeliverable → `suppressed`.
5. **Email copy** (`send_booking_cancelled`, house style of `send_booking_confirmed`:
   plain-text + HTML, `_p` helpers, details block): subject "Booking cancelled: <event>";
   event title, date/time, ticket type, quantity; "If you didn't expect this, reply to this
   email or contact the committee." If the booking was **confirmed** (paid) when
   cancelled, add a line that refunds for Union shop purchases are handled by the committee.
   Neutral voice — the system does not record who cancelled (section 12's audit log will).
   A link back to the event page so they can rebook if seats remain.
6. **Sender:** `services/cancellations.py` `send_pending()` + `trigger()` with the same
   shape as confirmations; run from the announcer loop (one line in `announce.py`).
   Latency up to ten minutes is acceptable; do not edit the cancel routes to call
   `trigger()` (they belong to other sections — noted as a follow-up in DECISIONS).
7. **Mail outage:** with mail not configured, claim nothing (same reasoning as
   confirmations — don't burn the retry budget during the annual password reset).

## Acceptance criteria

1. Stack: a member cancels their own held booking → one cancellation email to their
   address within one sender pass; state `sent`; a second pass sends nothing more.
2. Stack: committee cancels a confirmed booking → one email including the refund line;
   cancelling a held booking → email without the refund line.
3. Stack: a guest booking cancelled → email to the guest's address.
4. Stack: committee-added ticket with "email them" unticked, then cancelled → `skipped`,
   no email.
5. Migration: bookings already `cancelled` before 067 are `skipped` and never emailed.
6. Mail not configured → nothing claimed; once configured, the pending email goes out.
7. A send failure records `failed` and is retried on a later pass up to the same retry
   budget as confirmations; two overlapping passes never double-send (SKIP LOCKED claim).
8. Unit: `send_booking_cancelled` plain-text and HTML contain the event title, date, ticket
   type and the event link; the refund line appears only for a paid booking.
9. Existing suites stay green; pytest collects clean; compileall clean; migration
   expand-only; snapshot scrub/verify unaffected or updated.

## Constraints

- No Union API calls. Staging sends real mail from the shared mailbox — staging addresses
  are `@staging.invalid`, which `mailer.deliverable()` must keep suppressing (assert it).
- When sections 08/09 add whole-event cancellation, any bookings they cancel in bulk must
  be excluded from this sender (or marked `skipped`) so holders don't get two emails —
  record this in 08/09's specs when they're picked up.
- Comments explain why; commit messages `area: what changed`; never push.
