# Request 05 — doors close, prominent ticket, My tickets (2026-10-08)

> add a doors close datetime field for each event and make it obvious in each email. also
> make the option to show ticket / qr code very prominent on the event page and have a my
> tickets tab when logged in that gives a list and if clicked (and obv confirmed so qr code
> issued) then it directly brings to ticket, not event page

> the confirmation email should have the link to the ticket

**Triage: feature (inside the approved events/ticketing scope).** Raph asked for it
directly in an interactive session, which is his approval to build. Three sections:

- **14 — doors close** (new `events.doors_close_at`, admin field, event page, every
  event email, .ics).
- **15 — My tickets + prominent ticket on the event page** (after 14).
- **16 — confirmation email leads with the ticket link; free tickets get one too**
  (after 14, parallel with 15).

Findings that shaped the specs:
- The "you're confirmed" email *already* carries the ticket link — a "Open your ticket"
  button and a plain-text line — but the button sits **below the QR image**, which many
  mail clients hold back, so it reads as missing. And **free tickets get no email at all**:
  they stay `held` until the event, the confirmation sender only picks up `confirmed`
  bookings, and the held email is deliberately skipped for free tickets. A free booking
  has a QR and a ticket page but nobody is ever sent the link.
- The event page's ticket link is a small secondary "Show your ticket" button at the
  bottom of the "Your bookings" list.
- `/me/reservations` exists but has no ticket token and no page lists it.
