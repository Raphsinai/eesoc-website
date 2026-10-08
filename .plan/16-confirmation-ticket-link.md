# 16 — Confirmation email leads with the ticket; free tickets get one too

**Goal:** every booking that has a ticket gets a confirmation email whose first action is
the link to that ticket.

Request: `requests/05-doors-close-my-tickets.md` ("the confirmation email should have the
link to the ticket"). Depends on: 14 (shares `utils/email.py`, `database/routes/events.py`).
Parallel with 15 (disjoint files).

## Owns

- `backend/api/utils/email.py` (`send_booking_confirmed` only)
- `backend/api/services/confirmations.py`
- `backend/database/routes/events.py` (confirmation claim/result queries only)
- `backend/database/migrations/064_free_ticket_confirmations.sql` (new)
- `tests/confirmation-ticket-link/` (pytest)

## What to build

1. **Ticket link first** in `send_booking_confirmed`:
   - HTML: the "Open your ticket" button comes directly after the opening sentence — above
     the details rows and the QR image — so it shows even when the client blocks images.
     The QR image is itself wrapped in a link to the ticket. Keep the doors-close row from
     section 14 and the "Event details" note.
   - Plain text: "Your ticket (show this at the door): <url>" is the line right after the
     opening sentence, before Ticket/When/Where.
   - `ticket_url` becomes required (every caller has one); drop the `if ticket_url` guards.
2. **Free tickets get the confirmation.** A free booking (`ticket_types.requires_purchase`
   false) stays `held` until the event and today is never emailed. Make the claim query
   pick up live free holds as well as confirmed bookings — same `confirmation_email`
   state machine, same skip-after-the-event rule. Wording for both stays "Your place at
   X is confirmed" / subject "You're confirmed for X" (a free booking *is* a confirmed
   place). Ordering: `COALESCE(r.confirmed_at, r.created_at)` (or the equivalent column).
   Paid unpaid holds must still never be claimed.
3. **Catch-up send on rollout (Raph, `requests/06-qr-catch-up-send.md`):** everybody
   holding a ticket for an upcoming event who never received a QR gets the new
   confirmation email once, when this deploys. Migration 064 resets
   `confirmation_email` to `NULL` and `confirmation_attempts` to `0` for every booking
   that:
   - **has a ticket** — `status = 'confirmed'`, or a live free hold
     (`status = 'held' AND NOT tt.requires_purchase AND expires_at > NOW()`), and
   - is for an event that has **not started** (`e.starts_at > NOW()`), and
   - **never got a QR**: `confirmation_email IS NULL`, `'failed'`, `'suppressed'`, or
     `'skipped'` (the 059 backfill), or `'sent'` with `confirmation_sent_at` before
     **2026-10-08 20:15:00 UTC** — production sent confirmations *without* a QR from the
     a43c4b5 promote (16:28 UTC) until the QR email (34f0ac3) was promoted at 20:02 UTC;
     the 13-minute margin covers the rollout, and a rare duplicate is the acceptable
     failure, a missed ticket is not.
   - **Excluded:** `source = 'committee' AND confirmation_email = 'skipped'` — that is a
     committee member deliberately unticking "email them" when adding a ticket
     (`database/routes/events.py` committee insert); respect it. Also leave `'sending'`
     rows alone.

   The existing sender then sends them in its normal batches. Check how
   `send_pending`/`mailer.send` paces a burst through the one shared Imperial mailbox
   (`eesocweb@ic.ac.uk`, Exchange Online limits ~30 msgs/min); if nothing paces it, add a
   small delay between messages in `send_pending` so a few hundred catch-up emails cannot
   trip the mailbox's rate limit and lock out sign-in codes. Header comment in the style
   of 059 explaining all of the above. Record in `.plan/DECISIONS.md`.
   On staging this migration is harmless: addresses are scrubbed to `@staging.invalid`
   and the allowlist suppresses them. On production it runs at the promote Raph approves.
4. Update the docstrings that say a free ticket "gets nothing" where they are in owned
   files (the one in `routes/events.py` belongs to 15 — leave it).

## Acceptance criteria (pytest, `tests/confirmation-ticket-link/`)

1. Rendering `send_booking_confirmed` (monkeypatched `send`): in the HTML, the ticket URL's
   `href` appears **before** the `<img` of the QR and before the "Ticket" details row; the
   QR `<img>` sits inside an `<a href="<ticket_url>">`.
2. In the plain text, the ticket URL appears on the second non-empty line.
3. On the test stack: a new free booking is claimed by `/reservations/confirmations/claim`
   and, after `send_pending`, is `confirmation_email = 'sent'` or `'suppressed'`; a paid
   unpaid hold is not claimed.
4. A free booking whose event started more than 12h ago is `skipped`, not claimed.
5. Migration 064, run against seeded rows for an upcoming event: reset to `NULL` —
   a free live hold never emailed; a confirmed booking `'skipped'` by the 059 backfill;
   a confirmed booking `'sent'` at 2026-10-08 18:00 UTC. **Untouched** — `'sent'` at
   2026-10-08 21:00 UTC; committee-added `'skipped'`; a paid unpaid hold; an expired or
   cancelled booking; any booking for an event that has already started.
   Running 064 then `send_pending` sends exactly one email per reset booking.
6. Existing confirmation tests (committee-added, Mark bought) still pass.
