# Request 08 — email people when their ticket is cancelled (2026-10-10)

> cancelled tickets need to be informed by email

**Triage: tweak inside the approved ticketing scope → built this round as section 18.**
Spec: `.plan/18-cancellation-email.md`.

Finding: nothing tells a holder their booking was cancelled. Both cancel paths — the
member's own "Cancel" and the committee's cancel in the admin bookings list — end in the
same adapter `UPDATE … SET status = 'cancelled'` (`database/routes/events.py`
`cancel_reservation`), and no email is sent from either. Holds that lapse are not
cancelled (they stay `held` past their deadline, and the overdue view leaves the decision
to the committee by design), so "cancelled" means exactly that status.

Reading (recorded in DECISIONS.md):
- Every booking that becomes `cancelled` from now on gets one email, whoever cancelled it,
  sent the same durable way as the confirmation email (a per-booking state the sender
  claims, retries and records), so a future cancel path can't forget it either.
- The copy is neutral ("Your booking for X has been cancelled") because the system does
  not yet record *who* cancelled; it says to contact the committee if unexpected, and —
  for a booking that was paid — that refunds go through the committee.
- No email for historical cancellations (marked `skipped` by the migration), nor for a
  committee-added ticket whose holder was never told about it (the committee unticked
  "email them" and no confirmation went out).
- Whole-event cancellation notices belong to sections 08/09's approval queue; when those
  land they must not double up with this email (noted in 18's spec).
