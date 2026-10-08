# 13 — Door check-in: name and ticket type, obviously

**Goal:** at the door, every check-in (scanned or tapped from the list) shows the person's
name and ticket type big enough to read at arm's length on a phone, at a glance.

Request: `requests/04-checkin-ticket-type.md`. Depends on: nothing (the door screen is
landed). Frontend only — the API already returns `attendee.name`, `attendee.ticketType`,
`attendee.quantity` on `/checkin/scan`, `POST /checkin/reservations/{id}` and the
attendee list.

## Owns

- `frontend/src/views/CheckinView.vue`
- `tests/e2e/checkin-ticket-type/` (new Playwright specs)

Section 12 also lists `CheckinView.vue` (override gating) but has not touched it yet;
keep this change to the template/style plus the small script change below so 12 merges
over it trivially.

## What to build

1. **Scan result overlay** (`.scan__result`, the coloured panel over the camera):
   - Name: large and bold — at least 32px, wraps rather than truncating
     (`overflow-wrap: anywhere`), never clipped by the square camera frame (the panel may
     scroll or the type may step down for very long names, but it must stay readable).
   - Ticket type: its own prominent element, not inline text — a high-contrast pill
     (white background, dark text, ≥ 20px, bold) directly under the name. If
     `quantity > 1`, show "× N people" prominently next to/under it.
   - Headline (Checked in / Already in / Not paid / …) stays as is.
   - Shown for every outcome that has an `attendee` (checked_in, already, unpaid, invalid,
     wrong_event).
   - A good scan auto-dismisses after **3500ms** (was 2500ms — long enough to read the
     name and type). Tap-to-dismiss and the "decision" outcomes staying put are unchanged.
2. **Check-in from the list** (`checkIn()` / the row's "Check in" button): after a
   successful check-in show the same result panel — headline, big name, ticket-type pill —
   even when the camera is not open. When not scanning, render it as a panel in the
   `.scan` area above the list (same component markup, not over a video), auto-dismissing
   on the same timer, tap to dismiss. Pausing/resuming the scanner must keep working as
   today when the camera *is* open.
3. **List rows:** the ticket type becomes a visible tag next to/under the name (e.g. a
   `Badge` with a non-neutral tone) instead of only the grey mono meta text. Quantity
   "× N" stays alongside it. Shortcode/email stay in the meta line.
4. The unpaid `confirm()` prompt names the ticket type too
   (`"<name>'s <ticket type> booking is not paid. Let them in anyway?"`).

Match the file's existing style (design-system tokens, `Badge`/`Button` from
`components/ds`, scoped CSS). No new dependencies.

## Acceptance criteria (Playwright, `tests/e2e/checkin-ticket-type/`)

Use `page.route` to stub the `/api/checkin/...` endpoints so the specs need no seeded
data and no camera; for the scan path, call the component's scan handling via a stubbed
`/checkin/scan` response (e.g. fake `getUserMedia`/`BarcodeDetector` with
`page.addInitScript` returning a fixed QR string) — whichever is reliable.

1. **Scan shows name + type big:** a scan resolving to `checked_in` with
   `attendee {name: "Ada Lovelace", ticketType: "Member", quantity: 1}` shows a result panel
   containing the text "Ada Lovelace" and "Member"; the name element's computed
   `font-size` ≥ 32px and the ticket-type element's ≥ 20px; both are within the viewport
   and not clipped (bounding box inside the panel's) on a 390×844 viewport.
2. **Quantity:** same with `quantity: 3` shows "× 3 people".
3. **Other outcomes:** `already` and `unpaid` results with an attendee also show the name
   and ticket-type elements.
4. **List check-in confirmation:** with the camera closed, clicking "Check in" on a paid
   row whose POST returns `checked_in` shows the result panel with that row's name and
   ticket type; it disappears on tap.
5. **List row tag:** each attendee row renders its ticket type as a tag element (not only
   inside `.person__meta`).
6. **Unpaid prompt:** clicking "Check in" on an unpaid row raises a dialog whose message
   contains the ticket type.
7. Existing suites still pass; `npm run build` is clean.

## Commands

- Build: `cd frontend && npm run build`
- e2e: from repo root `npm ci && npx playwright install --with-deps chromium &&
  npx playwright test --config frontend/playwright.config.js` (needs the compose test
  stack — see `.plan/CODEBASE.md`; Docker is blocked in the build environment, so specs
  are validated with `node --check` + build locally and executed in CI. If the specs only
  use `page.route` stubs they may be runnable against `vite preview` if local port binding
  is allowed — try it, record the outcome.)
