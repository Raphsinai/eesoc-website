# 10 — Committee-added tickets: bookings created directly as confirmed

> **PROPOSED — awaiting approval** (request 01, round 2). Not buildable until approved.

**Depends on:** 05 (event fields, events routes), 06 (sign-up questions, QR confirmation,
CSV export, check-in), 08 (admin event surface). Build after 08; sequence with 09 (its
trigger hooks share routes with this section — coordinate via the planner; there is
**no queue involvement here**, a committee-added ticket's confirmation mail is
transactional and stays automatic).

## Goal

A committee member with the `events` capability can create a ticket **for** a person —
a member or a guest, e.g. someone who paid offline (cash, bank transfer) or a comp —
that lands immediately **confirmed**, skipping the reserve → Union-sale-match round
trip, while counting fully against the event's ticket allocation.

## Owns

- migrations `058`–`059` (booking `source`/`added_by` audit columns + a short committee
  note column — expand-only, per the rule in `deploy/README.md`)
- `backend/api/routes/events.py` and/or the admin event endpoints (create + cancel a
  committee-added booking — coordinate with what 08 left in place)
- `backend/database/routes/events.py` + `backend/database/models/events.py`
- `frontend/src/views/AdminEventView.vue` ("add ticket for someone" + cancel in the
  attendee list), `frontend/src/api/index.js`, `API.md`
- `deploy/snapshot/scrub.sql` (the committee note is free text → personal data)
- tests under `tests/committee-tickets/` and `tests/e2e/committee-tickets/`

## Behaviour

- **Create:** committee picks event + ticket type + holder (existing member by lookup,
  or guest name+email exactly like event guest booking), optionally answers that
  event's sign-up questions (06) and a short note ("paid cash"). The booking lands
  `confirmed` with `source = committee` and the actor recorded.
- **Sync safety:** a committee-added booking is never hold-expired, never cancelled by
  expiry, and never matched/duplicated against Union sales by the daily sync.
- **Allocation:** counts against the ticket type's capacity like any booking. Creating
  past capacity is refused — or waitlisted — exactly per the existing booking
  semantics; implementers must discover and match them, not invent new ones.
- **Cancel:** committee can cancel a committee-added ticket; capacity frees and the
  waitlist promotes per the existing rules.
- **Mail:** a transactional confirmation email to the holder via the same path as 06's
  booking confirmations (QR included once 06 has landed); `MAIL_ALLOWLIST` applies.
  No queue draft.
- **Downstream:** CSV export, live counts, QR check-in treat it identically to any
  confirmed booking.
- **Privacy:** the note and any answers get 06's visibility (`events` capability only),
  06's retention purge, and `scrub.sql` coverage.

## Acceptance criteria

1. Committee creates a member ticket → `confirmed`, allocation + live counts increment,
   row appears in the attendee CSV; a simulated sync day leaves it untouched.
2. Guest holder works identically; confirmation email arrives in the mail sink (with
   the QR once 06 has landed).
3. Over-capacity create is refused/waitlisted per existing semantics; cancelling frees
   capacity and promotes the waitlist per existing rules (both asserted).
4. Non-`events` committee users get 403 (API assert); an audit row records actor+time.
5. Note + answers purge with 06's retention window; a scrub run leaves staging clean.
6. `API.md` updated; existing booking/hold/waitlist tests stay green; migrations
   expand-only; `manifests` job green.

## Constraints

- No Union API calls from the new endpoints; no new eActivities traffic; no queue
  involvement.
- Never read `.env`/`backend.env`; comments explain why; commit style `area: what
  changed`.