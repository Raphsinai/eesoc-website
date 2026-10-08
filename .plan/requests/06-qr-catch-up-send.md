# Request 06 — catch-up QR email on rollout (2026-10-08)

> when it rolls out, send everyone that didn't get a qr code sent, the new confirmation
> email with qr code

**Triage: tweak to section 16.** Replaces 16's original "skip existing free holds" with a
one-off catch-up: migration 064 re-queues every ticket holder for an upcoming event who
never received a QR email, and the existing confirmation sender delivers them. Who that is,
from production's history:

- confirmed before 2026-10-08 16:28 UTC (a43c4b5 promote) — marked `skipped` by 059, no
  email at all;
- confirmed 16:28–~20:05 UTC — confirmation sent **without** a QR (QR email 34f0ac3 was
  promoted at 20:02 UTC);
- free bookings — never emailed at all.

Excluded: committee-added tickets where the committee unticked "email them", past events,
bookings without a ticket. Cutoff for "already got a QR": sent at or after 20:15 UTC.

"When it rolls out" = the production promote of section 16, which still needs Raph's
explicit approval after he has seen it on staging. Spec: `.plan/16-confirmation-ticket-link.md`.
