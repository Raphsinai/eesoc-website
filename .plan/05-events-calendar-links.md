# 05 — Events: calendar, links, target year groups

**Depends on:** 01 (tests). Parallel-safe with 02 and 03 (disjoint files: this owns the
events domain end-to-end; 06/07/08 depend on it).

Keep everything that exists (tickets, holds, waitlist, guest booking, sales-open,
product links, notify-me, sponsor claims, categories). This section only **adds**.

## Goal

Every event gets calendar integration and the full set of links; the society gets a
subscribable feed; admins can target year groups.

## Owns

- migrations `040`–`042` (new event columns: venue map link / online meeting link +
  private flag, external sign-up URL, target year groups, anything the feed needs —
  expand-only, per the rule in `deploy/README.md` from section 02)
- `backend/database/routes/events.py` + `backend/database/models/events.py`
- `backend/api/routes/events.py`
- `backend/api/routes/seo.py` (feed discovery links, if the feed is served there)
- `frontend/src/views/EventView.vue`, `frontend/src/views/EventsView.vue`,
  `frontend/src/api/index.js` (new client calls)
- `frontend/src/views/AdminEventView.vue` (editing the new fields)
- `API.md` (all new/changed endpoints)
- `deploy/snapshot/scrub.sql` (only if any new column can hold personal data — the
  target-year list and links should not; state this explicitly in the PR notes)
- tests under `tests/events-calendar/` and `tests/e2e/events-calendar/`

## Features

- **Per event:** `.ics` download (single event), Google Calendar link, map link for the
  venue, online-meeting link — shown only to booked attendees when the event marks it
  private, external sign-up link for events not sold through Union tickets.
- **Feed:** a subscribable calendar URL covering all published public events, plus a
  per-category variant. Published + public only: no attendee data, no private meeting
  links, no unpublished events. Unguessable-enough per-category URL is fine if it needs
  no auth; otherwise document the choice. Correct `Content-Type`, event times in UTC
  with timezone handled (events are wall-clock in Europe/London — see migration 031).
- **Target year groups:** a per-event setting (which year groups it is aimed at);
  editable in the admin event form. No emails yet — the queue (section 09) will read it.

## Acceptance criteria

1. `.ics` for an event validates (RFC 5545 parse in the test — use a library, don't
   eyeball it): correct start/end, summary, location, URL.
2. The all-events feed and a category feed both parse and contain exactly the published
   public events of scope; a private meeting link and an unpublished event never appear
   in either (negative tests).
3. Booked attendee (dev-login member with a confirmed booking) sees the private meeting
   link; a non-booked visitor does not (API + Playwright).
4. External sign-up link renders on the event page and is absent when unset.
5. Target year groups persist, round-trip through the admin form, and appear on the
   event API payload.
6. `.ics`/feed endpoints need no session (they're for calendar clients) and leak nothing
   beyond public event fields — assert the JSON/ics payload against a field allowlist.
7. Existing event behaviour unchanged: booking, holds, waitlist, sales-open, notify-me
   still pass (existing tests + new regression asserts).
8. `API.md` updated; `manifests` job green; scrub.sql only extended, never weakened.

## Constraints

- Migrations expand-only (old api keeps working against them — see the rule 02 wrote
  into `deploy/README.md`).
- Nothing calls the union service from a request path; no new eActivities traffic.
- Never read `.env`/`backend.env`; comments explain why; commit style `area: what changed`.
