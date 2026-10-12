status: in_progress

# Plan: zero-downtime deploys, richer events, email approval queue — plus ticketing follow-ups

Original brief: `BRIEF.md`; intake survey: `CODEBASE.md`; change requests: `requests/`;
every assumption: `DECISIONS.md`. **Staging only; nothing is promoted without Raph.**

## Build progress

| # | Section | Status |
|---|---|---|
| 01 | Baseline tests | merged |
| 02 | Zero-downtime rollout | merged (live rollout verification still a human step) |
| 03 | SPA asset continuity | merged (+ BusyBox `cp -an` fix landed with the passkeys work) |
| 04 | Staging refresh without downtime | not started (depends on 02 — unblocked) |
| 05 | Events: calendar, links | merged |
| 06 | Sign-up questions (rescoped) | not started (after 12) |
| 07 | Rich event pages, archive | built on `plan-07-rich-events`, **unmerged**; wave 2 (merge main in, re-validate) |
| 08 | Event admin workflow | not started (after 07) |
| 09 | Email approval queue | **core landed out-of-plan 2026-10-12** (Raph's direct requests, migrations 073–074): queue, edit words + recipients, test send, send now / approve for its time (scheduled) / dismiss, BCC send. The on-sale draft is written when an event is saved with a sale time and follows the event (unedited drafts rewritten; approvals held if approved words would change or the event is unpublished). Still to do from the spec: the other triggers, staleness |
| 10 | Committee-added tickets | superseded — landed on the origin line (a43c4b5) |
| 11 | Form builder | proposed, **awaiting approval** (migrations to renumber to 070–071 when approved) |
| 12 | Ticket perms + audit log | wave 1, building (migrations 068–069) |
| 13 | Check-in ticket type | merged |
| 14 | Doors close | merged |
| 15 | My tickets | wave 1, finishing (built on `plan-15-my-tickets`; tests were uncommitted) |
| 16 | Confirmation email ticket link + catch-up | wave 1, finishing (built on `plan-16-confirmation-ticket-link`) |
| 17 | Sale windows + end-of-day hold deadlines | **proposed, awaiting approval** (note of 2026-10-10) |
| 18 | Cancellation email | wave 2 (note of 2026-10-10; after 16) |

Out-of-plan work landed and pushed since round 4 (Raph's direct sessions): passkeys
(migration 065), CI made to actually run pytest + e2e, uvicorn entrypoint fix, and
(2026-10-12) the email approval queue core — see row 09. Its ruling, which departs
from 09's spec: the per-event "tell me when it's on sale" askers are **not** sent
automatically any more; they are one ticked audience on the on-sale draft and go
out BCC'd with everybody else when it is approved ("every automated email needs
approval, only emails directed to people can be sent automatically"). Migrations
**073–074** are taken (072 is the uncommitted free-tickets work in the main
checkout). Raph's rule from the same day: recurring jobs run **hourly, on the
hour**, never more often (utils/clock.py) — the reminder pass moved from every 10
minutes; a scheduled email is a timer for its moment, not a recurring job.

## This round (2026-10-10)

- Notes triaged: `requests/07-sale-end-hold-deadline.md` → section 17 (proposed);
  `requests/08-cancellation-email.md` → section 18 (built this round).
- Wave 1: 15 ∥ 16 ∥ 12. Wave 2: 18 ∥ 07.

## Awaiting approval

**Section 17 — sale windows + hold deadlines** (`.plan/17-sale-windows-hold-deadlines.md`).
Two optional event times: *reservations close* (nobody can reserve after) and *Union
sales close* (no hold deadline may pass it — booked 12:00, 1-day hold, Union close 16:00
→ deadline 16:00). Every hold deadline becomes the end of a London day: an N-day hold
ends 23:59 on booking day + N−1, one day later if booked at/after 23:00. **One thing to
confirm:** your note's first sentence could mean a 1-day hold booked at noon runs to the
end of *tomorrow*; the spec reads it as end of *today* (rolling to tomorrow after 23:00),
which matches the rest of the note. Say which when approving.

## Blockers (human steps)

1. `git push` is denied from the planner session — Raph pushes `main` (that deploys to
   staging via CI; CI now really runs pytest + e2e, so it is the execution gate).
2. Production promotes are Raph's only (`gh workflow run promote.yml`), per change.
3. Section 02's live rollout check (`scripts/watch-rollout.sh https://eesoc.raphsinai.dev`)
   during a staging deploy.
