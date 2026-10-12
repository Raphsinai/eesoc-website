# 17 — Sale windows and end-of-day hold deadlines

**Status: BUILT 2026-10-12** (approved by Raph the same day; hold ends at midnight the
*same* day, rolling to the next after 23:00 — the reading below). Built on main after the
free-tickets work landed, not after 07/12/15/16 as originally sequenced; those branches
merge on top. Additions beyond the spec: guest seats and waitlist joins are gated too;
last-chance reminders now go ~6h before the deadline and never within 2h of booking
(a 24h lead fires straight after booking once holds end at midnight).

**Goal:** an event can stop taking reservations at one time and stop Union shop sales at
another; every hold's deadline is the end of a London calendar day (rolling to the next
day after 23:00) and never later than the Union sales close.

Request: `requests/07-sale-end-hold-deadline.md` (readings and the one flagged ambiguity
are there). Depends on: 15, 16, 12, 07 merged — all of them edit the booking/event files
this section owns; it builds on top rather than in parallel.

## Owns

- `backend/database/migrations/066_event_sale_windows.sql` (new, expand-only):
  `events.reservations_close_at TIMESTAMPTZ NULL`, `events.union_sales_close_at
  TIMESTAMPTZ NULL`, a CHECK that `reservations_close_at <= union_sales_close_at` when
  both are set.
- `backend/database/routes/events.py` — event create/update/list columns, the
  `HOLD_EXPIRES` expression, the reserve gate, waitlist offer expiry; `models/events.py`.
- `backend/api/routes/events.py`, `backend/api/routes/admin.py` — field passthrough,
  validation, public shape (`reservationsCloseAt`, `unionSalesCloseAt`, and a computed
  `bookingOpen`).
- `backend/api/utils/holds.py` (new) — the pure Python reference implementation of the
  deadline rule, used by the tests and anywhere the API needs to show a deadline before
  the booking exists.
- `frontend/src/views/AdminEventView.vue` (two datetime fields; hold length in days),
  `frontend/src/views/EventView.vue` ("Booking closes …", "Buy on the Union shop by …",
  the reserve button disabled with a reason once closed).
- `API.md`; tests under `tests/sale-windows/` and `tests/e2e/sale-windows/`.

## The rule

For a hold of `N` days (`N = max(1, ceil(hold_hours / 24))`, ticket-type override first,
as today) created at instant `t`:

1. `d` = the Europe/London calendar date of `t`; if the London wall-clock hour of `t` is
   ≥ 23, `d = d + 1`.
2. deadline = 23:59:59 Europe/London on `d + (N − 1)` days.
3. If the event has `union_sales_close_at`, deadline = `min(deadline, union_sales_close_at)`.
4. Holds on a ticket with **no Union product linked** keep today's rule (they last until
   the event starts — nobody can buy them on the shop; the committee confirms by hand).

DST must be right: compute in Europe/London, not UTC (a hold made on the last Sunday of
October or March gets the correct local end of day). The SQL expression and
`utils/holds.py` must agree — tested across both DST changes and the 23:00 boundary
(22:59:59 vs 23:00:00).

## What to build

1. Migration 066 as above. No backfill of existing holds' deadlines.
2. **Reserve gate:** once `reservations_close_at` has passed, the reserve endpoint
   (members, guests, guest drafts, waitlist-offer claims, ticket changes that create a new
   hold) refuses with a clear reason (`reservations_closed`) — enforced in the database
   adapter, not only the SPA. Committee-added tickets are not gated (committee override,
   and section 12 already restricts who can add them).
3. **Waitlist offers:** an offer's `offer_expires_at` is also capped at
   `union_sales_close_at`; no offers are made after `reservations_close_at`.
4. **Deadline rule** in `HOLD_EXPIRES` per "The rule".
5. **Moving the close earlier:** when an admin saves a `union_sales_close_at` earlier than
   some live holds' deadlines, those holds' `expires_at` is clamped to it in the same
   transaction (and the change is visible in the bookings list's "Hold until").
6. **Admin:** two optional datetime inputs with one-line help text each; hold length edited
   in whole days (stored as `days × 24`); validation error if reservations close after the
   Union close, or either is after the event ends.
7. **Event page:** shows "Booking closes <when>" while open, "Booking has closed" after,
   and the purchase deadline on a held booking already reflects the new rule (it is read
   from `expires_at`).
8. Emails already print `expires_at` as "Held until" — check the held/reminder/last-chance
   copy still reads correctly for an end-of-day deadline (e.g. "Held until Sat 11 Oct,
   23:59"), and that last-chance timing (if relative to the deadline) still makes sense.

## Acceptance criteria

1. Unit (`utils/holds.py`): 1-day hold at 12:00 → same day 23:59:59 London; at 22:59:59 →
   same day; at 23:00:00 → next day 23:59:59; 2-day hold → +1 day in each case; correct
   across the BST→GMT and GMT→BST change days.
2. Stack: a hold created via the API on a linked ticket gets exactly the deadline the
   reference function computes (SQL and Python agree), including with
   `union_sales_close_at` 16:00 the same day → deadline 16:00.
3. Stack: unlinked ticket holds still last until the event starts.
4. Stack: after `reservations_close_at`, member reserve, guest reserve and waitlist claim
   all fail with `reservations_closed`; a committee-added ticket still succeeds.
5. Stack: saving an earlier `union_sales_close_at` clamps existing live holds; confirmed
   bookings are untouched.
6. Stack: the matcher still confirms a hold whose deadline passed overnight when the
   matching sale's datetime is before the deadline (the 06:05 sync case).
7. Admin validation: reservations close after Union close → 422 with a field error.
8. Playwright: admin sets both times and a hold length in days; the event page shows
   "Booking closes …" and, once past, the reserve button is disabled with the reason.
9. Unset fields → behaviour identical to today apart from the end-of-day rule (regression
   suites from 12/13/14/15/16 stay green). Migrations expand-only; `API.md` updated.

## Constraints

- No new Union API calls; the sync and matcher call paths are untouched (AC6 is a
  regression test, not a change).
- Comments explain why; commit messages `area: what changed`; never push.
