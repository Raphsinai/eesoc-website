# 15 — My tickets, and the ticket front and centre on the event page

**Goal:** a signed-in member can reach their QR ticket in one tap — from a "My tickets"
tab listing every booking, and from a prominent ticket block on the event page.

Request: `requests/05-doors-close-my-tickets.md`. Depends on: 14 (shares
`EventView.vue` and `routes/events.py`; uses `doors_close_at`/`venue` on the
reservations query). Parallel with 16 (disjoint files).

## Owns

- `frontend/src/views/MyTicketsView.vue` (new), `frontend/src/router/index.js`,
  `frontend/src/components/SiteHeader.vue`
- `frontend/src/views/EventView.vue` (the "Your bookings" ticket block)
- `frontend/src/views/TicketView.vue` (a "← My tickets" link only, for signed-in users)
- `backend/api/routes/events.py` (`/me/reservations`; the one `confirmations.trigger()`
  line in item 5)
- `tests/my-tickets/` (pytest), `tests/e2e/my-tickets/` (Playwright)

## "Has a ticket"

A booking has an issued ticket (QR) exactly when the event page already shows "Show your
ticket": `status == 'confirmed'`, or a free ticket type (`requires_purchase` false) whose
hold is still live. Use one helper for this in the api so My tickets and the event page
cannot disagree.

## What to build

1. **`GET /api/me/reservations`** adds, per row: `hasTicket` (rule above), `ticketToken`
   (`services.tickets.token_for(id)`, **only** when `hasTicket`, else `null` — an unpaid
   hold must not leak a scannable token), `requiresPurchase`, `venue`, `doorsCloseAt`.
2. **My tickets page** at `/tickets` (route name `my-tickets`, title "My tickets",
   `noindex`, signed-in only — signed-out visitors are sent to sign in the way other
   member-only pages do, or to `/` if there is no such pattern).
   - Two groups: **Upcoming** (event start ≥ now − 12h, soonest first) and **Past** (most
     recent first, collapsed or below). Cancelled/expired bookings are hidden.
   - Each row: event title, date/time, venue, ticket type × quantity, a status badge
     ("Ticket ready" for `hasTicket`, "Awaiting payment" for an unpaid hold), and the
     doors-close time when set.
   - **Clicking a row with `hasTicket` goes straight to `/ticket/{ticketToken}`** — not the
     event page. A row without a ticket goes to `/events/{slug}` (where they can pay).
     A secondary "Event details" link on ticket rows still reaches the event page.
   - Empty state: "No tickets yet" with a link to `/events`.
3. **Nav:** "My tickets" appears in `SiteHeader` (desktop nav and mobile menu) only when
   the session is authenticated; hidden when signed out.
4. **Event page ticket block:** when any of the viewer's bookings `hasTicket`, show at the
   **top** of the booking card (above the ticket-type picker / booking form, not under
   "Your bookings") a prominent block: "You have a ticket" heading, ticket type × qty, a
   small inline QR (`/api/qr/{token}.png`, ~120px, itself a link) and a full-width primary
   button "Show your ticket". Replace the current small secondary "Show your ticket" button.
   Visible in the first viewport on a 390×844 phone. A booking without a ticket keeps the
   existing "Your bookings" treatment.
5. **Free bookings email immediately:** after a free-ticket booking is taken in the
   booking route, call the existing `services.confirmations.trigger()` so section 16's
   free-ticket confirmation goes straight away rather than on the next ten-minute loop.
   (`trigger()` already exists and is safe to call; this section does not depend on 16.)
6. **TicketView:** a "← My tickets" link for signed-in users.

## Acceptance criteria

pytest (`tests/my-tickets/`):
1. `/me/reservations` for a confirmed booking: `hasTicket` true and `ticketToken`
   verifies with `tickets.parse`.
2. Free live hold: `hasTicket` true with a token. Paid unpaid hold: `hasTicket` false,
   `ticketToken` null.
3. Rows include `venue`, `doorsCloseAt`, `requiresPurchase`.
4. Signed out → 401.

Playwright (`tests/e2e/my-tickets/`, stub APIs with `page.route` where seeding is awkward):
5. Signed in: header shows "My tickets"; signed out: it does not.
6. `/tickets` lists upcoming bookings; clicking a `hasTicket` row lands on
   `/ticket/<token>` (URL check), clicking an unpaid row lands on `/events/<slug>`.
7. Empty list shows "No tickets yet".
8. Event page with a ticketed booking shows "Show your ticket" as a primary button inside
   the first viewport at 390×844, above the booking form, and it links to `/ticket/<token>`.

Plus: existing suites still pass, `npm run build` clean.
