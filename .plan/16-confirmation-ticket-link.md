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
3. **No surprise mass send on deploy:** migration 064 marks existing live free holds whose
   `confirmation_email IS NULL` as `'skipped'`, so shipping this emails only bookings made
   from now on. Record the choice in `.plan/DECISIONS.md` (Raph can still re-send to a
   person from the bookings screen if one exists; if none exists, say so in DECISIONS).
   Expand-only data migration; header comment in the style of 059.
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
5. Migration 064: a free hold existing before it runs ends up `'skipped'`; one created
   after is claimed.
6. Existing confirmation tests (committee-added, Mark bought) still pass.
