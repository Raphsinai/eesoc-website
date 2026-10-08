# Request 04 — ticket type obvious at check-in (2026-10-08)

> can you add the ticket type obviously when checking in and make sure that a scanned qr
> code displays quite obviously the name and ticket type

**Triage: tweak** — inside the approved ticketing/check-in scope. No backend change: the
door API already returns `name` and `ticketType` for every attendee and scan result.
Today the door screen does show them, but small: the scan overlay has the name at 22px and
the ticket type as plain 16px text; the list row has the ticket type in the grey mono
`xs` meta line; a check-in from the list gives no confirmation at all unless the camera is
open.

Spec'd as section 13 (`.plan/13-checkin-ticket-type.md`).
