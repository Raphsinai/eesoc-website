# Request 03 — ticket perms + a detailed audit log

**From Raph, 2026-10-08 (round 4, directly in chat — this is his approval, not a
note awaiting it):**

> carry on and change the perms so that only admin and superuser can manually
> confirm tickets and add tickets to events and keep a log, a detailed log to
> trace who did what

## Triage

Feature — a scope change on the landed ticketing system (origin line, commits
`34f0ac3` + `a43c4b5`): tighter permissions **and** an audit log. Planned as
**section 12** (`.plan/12-ticket-perms-audit.md`); Raph instructed it be built
this round, so it is not parked in awaiting-approval.

## Reading

- **"manually confirm tickets"** = the door override ("Let in anyway" for an
  unpaid/cancelled ticket, `POST /checkin/reservations/{id}` with
  `override: true`) and the admin confirm-a-booking action
  ("Mark bought"-class). Both land a booking in `confirmed` outside the
  automatic Union-sale match. The automatic match itself is NOT restricted —
  that is the system, not a person.
- **"add tickets to events"** = committee-added tickets (`source =
  'committee'`, `added_by`) and their cancel/ticket-type-change siblings.
- **"only admin and superuser"** = roles `admin` and `superuser` in
  `backend/api/utils/roles.py`. Cleanest fit is a new capability granted to
  exactly those two, per the file's capabilities-not-roles philosophy.
- **"keep a detailed log"** = a per-action audit trail (who, what, to which
  booking/event, when, with what detail) covering manual confirms, overrides,
  ticket adds/cancels/type changes, door check-ins and undos — and system
  actions (the sync's automatic confirms) so "who did what" can answer "the
  sync did, matched to sale N" vs "the VP let them in manually".
- **Not a change:** the door screen itself stays open to the whole committee
  roster (`require_committee`) — scanning, name search and ordinary
  check-in/undo are unchanged. Only the *override* and *add/confirm* paths
  tighten.

## Behaviour this supersedes

Spec 10 (`.plan/10-committee-added-tickets.md`) gated adds on the `events`
capability. Its core already landed out-of-plan on the origin line
(`a43c4b5`, promoted to production before this request). Section 12 tightens
the landed behaviour to admin/superuser per this instruction; spec 10's
remaining ideas (questions on committee tickets, waitlist semantics) stay in
its file for the 06/08 rescope to absorb. Recorded in DECISIONS.md.