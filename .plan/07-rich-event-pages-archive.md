# 07 — Events: rich pages and the past-events archive

**Depends on:** 05 (events model/routes/views it extends). 08 depends on this.

## Goal

Event pages carry agenda items, speakers, linked sponsors, co-hosting societies, and —
after the event — a photo gallery and slides/resources; plus a searchable/filterable
past-events archive.

## Owns

- migrations `047`–`049` (agenda items, speakers, co-host societies, gallery items,
  post-event resources links — expand-only)
- `backend/database/routes/events.py` + `models/events.py` (new sub-resources),
  `backend/database/routes/sponsors.py` only if the sponsor link table lives there
- `backend/api/routes/events.py` (public shapes + admin sub-resource endpoints), or a
  new `backend/api/routes/event_content.py` if that keeps the file sane — pick one and
  say why
- upload handling for speaker photos / gallery: extend the existing media flow
  (`services/storage.py` conventions; 02's retry behaviour must survive)
- `frontend/src/views/EventView.vue`, `frontend/src/views/EventsView.vue`,
  `frontend/src/views/ArchiveView.vue` (new), `frontend/src/router/index.js`,
  `frontend/src/api/index.js`, `frontend/src/components/` (new components for
  agenda/speakers/gallery)
- `frontend/src/views/AdminEventView.vue` (editing all of the above)
- `API.md`, `deploy/snapshot/scrub.sql` (only if any new column holds free text about
  identifiable people — speaker bios are public content, not personal data; say so
  explicitly)
- tests under `tests/rich-events/` and `tests/e2e/rich-events/`

## Features

- **Agenda/schedule:** ordered items with time, title, optional description.
- **Speakers:** name, title/affiliation, photo (upload), bio. Public content.
- **Linked sponsors:** attach existing sponsor records to an event (beyond the current
  claim mechanism — additive, don't break sponsor claims).
- **Co-hosting societies:** free-form names/links.
- **Post-event:** photo gallery (multiple uploads, orderable) and slides/resources links
  (files or URLs), added by `events`-capable committee after the event.
- **Archive:** past events (event end in the past), filter by category and year, free
  text search over title/description; server-side filtering (not client-side-only) so it
  holds at archive scale; the search must not leak unpublished or non-public events.

## Acceptance criteria

1. Admin can set agenda items, speakers (with photo upload), linked sponsors,
   co-hosting societies on an event; each renders on the event page in the admin's
   order (Playwright).
2. Gallery and resource links render on a past event; uploading a gallery photo goes
   through the existing media path and is served by nginx off the media volume (assert
   the image URL serves with the right content type).
3. Archive: create a spread of past/current/future, published/unpublished events → the
   archive lists only published past events; category filter, year filter, and text
   search each narrow correctly (API tests + one Playwright pass).
4. Unpublished or private content (private meeting link) never appears in archive
   payloads or search results (negative tests).
5. Existing event page fields and booking flows unchanged (regression tests green).
6. `API.md` updated; `manifests` job green; migrations expand-only.
