# 14 — Doors close time on every event, obvious in every email

**Goal:** each event can carry an optional "doors close" datetime, set in the committee
admin, shown prominently on the event page and in every email about that event.

Request: `requests/05-doors-close-my-tickets.md`. Depends on: nothing. Blocks 15 and 16
(they edit files this section owns).

## Owns

- `backend/database/migrations/063_event_doors_close.sql` (new)
- `backend/database/models/events.py`, `backend/database/routes/events.py`
- `backend/api/utils/present.py`, `backend/api/utils/ics.py`, `backend/api/utils/email.py`
- every caller of an event email: `backend/api/services/{announce,sync,confirmations}.py`,
  `backend/api/routes/events.py` (`_send_held_confirmation`), `backend/api/routes/admin.py`
  (purchase reminder, event announcement) — only the lines that pass the new value through
- `frontend/src/views/AdminView.vue` (event form), `frontend/src/views/EventView.vue`
  (display only)
- `tests/doors-close/` (pytest), `tests/e2e/doors-close/` (Playwright)

## What to build

1. **Schema:** `ALTER TABLE public.events ADD COLUMN IF NOT EXISTS doors_close_at
   TIMESTAMPTZ;` — nullable, expand-only (the previous image must keep working against it;
   follow the header-comment style of `059_booking_confirmations.sql`). Not personal data,
   so no `deploy/snapshot/scrub.sql` change.
2. **Model/routes:** `doors_close_at: datetime | None` on the event create/update/read
   models; returned wherever the event is read. Validation: if both are set,
   `doors_close_at` must not be after `ends_at` (422 with a clear message). It may be
   before `starts_at` (e.g. doors close at 18:00 for an 18:30 talk). The per-user
   reservations query (`GET /reservations?user_id=`) also returns `e.doors_close_at` and
   `e.venue` — section 15 needs them for My tickets.
3. **Admin form** (`AdminView.vue` event editor): a "Doors close" `datetime-local` field
   next to Starts/Ends, with help text "Optional. Shown on the event page and in every
   email — no entry after this time." Same local↔ISO handling as `ends_at`.
4. **Public payload** (`present.py`): `doorsCloseAt` (ISO) plus a ready-formatted
   `doorsClose` string (e.g. "18:15", or "Thu 9 Oct, 18:15" when not the same day as the
   start), using the existing date/time formatters.
5. **Event page:** when set, a high-visibility line beside the date/time —
   "Doors close 18:15 — no entry after" — styled as a callout (not muted text), visible
   without scrolling on a 390px phone and on desktop.
6. **Emails:** every email about a specific event shows the doors-close time when the
   event has one, as an emphasised row in the details block (HTML: bold, its own row
   labelled "Doors close", visually distinct — e.g. a warning-tinted row) and a
   `Doors close: …` line in the plain-text part near the "When" line. Applies to:
   `send_booking_held`, `send_booking_confirmed`, `send_last_chance`,
   `send_waitlist_offer`, `send_purchase_reminder`, `send_tickets_on_sale`,
   `send_event_announcement`. Add an optional `doors_close: str | None = None` parameter
   to each and pass it from every caller (the callers already load the event; extend
   their queries if `doors_close_at` isn't there yet). Absent → nothing rendered.
   Put the shared row rendering in one helper next to `_detail_rows`.
7. **.ics:** append "Doors close 18:15 — no entry after." to the event DESCRIPTION when set.

Section 08 (not yet built — duplicate/series) should copy `doors_close_at` when it
duplicates an event; record that in `.plan/DECISIONS.md`.

## Acceptance criteria

pytest (`tests/doors-close/`):
1. Migration 063 applies on the test stack; an event created without the field reads back
   `doors_close_at = null`; one created/updated with it reads it back.
2. Update with `doors_close_at` after `ends_at` → 422; before `starts_at` → accepted.
3. Public `GET /api/events/{slug}` includes `doorsCloseAt` and `doorsClose` when set,
   `null` when not.
4. Each of the seven email functions, called with `doors_close="18:15"`, produces HTML and
   plain text both containing "Doors close" and "18:15"; called without it, neither
   contains "Doors close". (Unit-level: monkeypatch `send` to capture the message rather
   than sending.)
5. The event `.ics` DESCRIPTION contains the doors-close line when set.
6. `GET /reservations?user_id=` rows include `doors_close_at` and `venue`.

Playwright (`tests/e2e/doors-close/`):
7. Committee admin sets "Doors close" on an event, saves, reloads: the value persists.
8. The public event page shows "Doors close" with the time, within the first viewport at
   390×844.

Plus: existing suites still pass, `npm run build` clean.
