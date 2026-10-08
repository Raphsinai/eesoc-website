# 06 (rescoped, round 4) — Sign-up questions, answers, export; tests for the landed ticketing

> **Rescoped 2026-10-08 after the reconciliation merge (2582c63).** The original
> 06 (questions + QR tickets + CSV + check-in screen) was partly built out-of-plan
> by the origin line (commits 34f0ac3, a43c4b5) and has landed on `main`. Gap
> analysis vs this spec's ACs is recorded in `requests/03-*`'s companion report and
> `DECISIONS.md`; this file now specs **only the remainder**. Everything the origin
> line built is a dependency, not something to recreate.

**Depends on:** 12 (ticketing perms + audit log — merges first; both touch
`AdminEventView.vue` and `routes/admin.py`). Builds on the landed ticketing
surface (tokens, door check-in, committee adds) and on 02's job-lease machinery
(migrations 035–037) for the retention purge.

## Already landed — do NOT rebuild (verify, don't re-implement)

- Signed ticket tokens (`services/tickets.py`: reservation-id + HMAC over
  `emailing._secret()`, `compare_digest`, 400 on tampered payload), `/ticket/<token>`
  view + QR PNG endpoint, the QR in the confirmation email and the EventView ticket
  CTA (`utils/email.py`, `routes/events.py`, `EventView.vue`).
- The door: `routes/checkin.py` (events/list/state/scan/manual/undo, public ticket
  + QR endpoints), `frontend/src/views/CheckinView.vue` (polling, BarcodeDetector +
  lazy jsQR, name search, overrides, undo), `migrations/060_checkins.sql`
  (**one row per reservation** — this file is landed and immutable; the old 06's
  planned `045` checkins migration is dead, never write it).
- Committee-added tickets (`routes/admin.py`, `database/routes/events.py`,
  `059_booking_confirmations.sql`: `source`, `added_by`, per-booking confirmation
  email state incl. MAIL_ALLOWLIST suppression).
- Section 12 (in flight) tightens who may override / add / manually confirm and
  adds the audit log; **this section must not touch those gates or the audit log** —
  build beside them.

## Owns (the remainder)

- migrations `043`–`044` (questions; answers — expand-only; `045`–`046` reserved if
  needed, but **060 is taken** by the landed checkins, so never go there)
- `backend/api/routes/events.py`: the questions surface (admin CRUD per event) and
  answer submission on booking reserve (member + guest), answers visibility
- `backend/database/routes/events.py` + `models/events.py`: question/answer queries
- A **server-side** CSV export endpoint (the landed CSV in `AdminEventView.vue:227-242`
  is client-generated and carries no answers — extend or replace per spec below)
- `backend/api/services/jobs.py` or a sibling: the answers retention purge riding
  02's lease-guarded job mechanism (never a new scheduler)
- `frontend/src/views/EventView.vue` (booking form questions for member + guest),
  `frontend/src/views/AdminEventView.vue` (question editor; answers in the door
  list / CSV button — coordinate with 12's layout)
- `API.md`, `deploy/snapshot/scrub.sql` (answers hold personal data)
- tests under `tests/checkin/` + `tests/e2e/checkin/` — **both the new questions
  feature AND regression coverage for the whole landed ticketing surface**
  (tokens incl. tampered, door scan/manual/override/undo idempotence, committee
  add incl. guest holder + over-capacity 409 + cancel/waitlist promotion,
  confirmation-email allowlist suppression, `060` idempotence)

## Design notes

- Questions: text / single-choice / multi-choice, required/optional, ordered.
  Answers submitted with the booking reserve; missing required answers → 422/400
  naming the question. Choices round-trip.
- **Answers visibility is `events`-capability, endpoint-enforced** — not
  `require_event_editor` (which also admits `industry` for industry events —
  correct for editing, wrong for personal data; the export and any answer-bearing
  endpoint use a strict `require_capability("events")`-style guard). Guests' answers
  are the same class of data as members'.
- The door (`require_committee`) stays whole-roster — superseded by 12/Raph's
  round-4 instruction; the old 06 design note that wanted `events`-capability-only
  on the door is dropped.
- CSV: server-side, `text/csv` + content-disposition, one row per attendee with
  answers; paginated or streamed so a big event doesn't block the loop; `events`
  capability required. Keep the client-side door-list CSV only if it holds no
  answers (assert it doesn't).
- Retention: answers (and guest name/email rows per the landed privacy model)
  purge 30 days after the event ends, configurable via env/config with the 30-day
  default; a lease-guarded job (02's `jobs` table), restart-safe, never blocking
  requests. **The audit log from 12 is explicitly excluded** — it exists to be
  durable; 12's scrub handles it.
- `scrub.sql`: answers columns/tables truncated like the landed checkins
  truncation.

## Acceptance criteria (rescoped)

1. Admin defines questions (3 kinds; required/optional) on an event; a member
   booking must answer required ones (API rejects missing, naming the question),
   optional persist; choices round-trip (pytest + Playwright).
2. Guest booking answers behave identically (pytest).
3. Server-side CSV: one row per attendee **with answers**; `events` capability
   required (a committee member without it → 403); no answers in any public or
   member-facing payload (negative assertions).
4. The landed surfaces stay green: tokens tamper-rejected (400), door scan/manual
   check-in idempotent with clear duplicate reporting, override gated by 12,
   undo works, committee add (guest + member) lands confirmed with
   `source='committee'`, over-capacity 409, cancel promotes the waitlist,
   confirmation mail suppressed under a staging allowlist fixture (all pytest;
   one Playwright door pass).
5. Retention purge: with the window wound down in a test, answers are gone after
   the event+window while bookings, tickets and 12's audit rows survive; window
   configurable (pytest).
6. `scrub.sql` covers every new personal-data column/table; migration 043–044
   expand-only; `API.md` documents questions/answers/export/retention.
7. Static gates: `env/bin/python -m pytest tests --collect-only -q` clean,
   `python3 -m compileall backend scripts`, `node --check` every spec,
   `npm run build` clean; router stays lazy-chunked (03's tests unregressed).

## Constraints

- No Union API calls anywhere; no queue involvement (booking confirmations stay
  transactional); never read `.env`/`backend.env`/`env/`; comments explain why;
  commit style `area: what changed`; migrations immutable once shipped.