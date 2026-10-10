# 12 — Ticketing permissions + a detailed audit log

**Depends on:** nothing in this plan — builds on the landed ticketing system
(origin line, merged as of round 4). Must not touch the events sign-up
questions surface: the rescoped section 06 (questions/answers/CSV/retention)
sequenced after this section owns the admin-event editing it needs. If this
section lands first, 06's rescope builds on its audit helper.

## Goal

Only `admin` and `superuser` may manually confirm tickets and add tickets to
events; every ticketing action — manual or automatic, door or admin — leaves a
detailed, traceable audit row saying who did what, to which booking, when.

## Owns

- migrations `068`–`069` (audit log; any recording columns 059/060 lack — expand-only;
  renumbered 2026-10-10 from 061–062 to land past passkeys' 065)
- `backend/api/utils/roles.py` (a new capability granted to exactly `admin` +
  `superuser`; blurbs updated so the People tab is accurate)
- `backend/api/utils/deps.py` only if a guard helper needs shaping
- `backend/api/routes/checkin.py` — gate the `override` path and undo-of-override
  on the new capability; audit rows for check-in/undo/override
- `backend/api/routes/admin.py` — gate committee-added-ticket create/cancel,
  ticket-type change, and manual booking confirm on the new capability; audit
  rows for each; the audit-log list endpoint
- `backend/api/services/confirmations.py` / `services/purchase.py` (or wherever
  the confirm action actually resolves) — audit the automatic sync-confirm as
  a system action; **do not** touch `services/sync.py`'s Union call paths.
  **2026-10-10:** section 16 owns `services/confirmations.py` and the confirmation
  queries in `backend/database/routes/events.py` in parallel — do not edit either.
  Record the sync confirm without them (e.g. an `AFTER UPDATE` trigger on
  `reservations` in your own migration, firing when a row becomes `confirmed` with a
  `matched_sale_id`, writing a system-actor row); manual confirms/cancels are audited
  in the api layer (`routes/admin.py`) after the adapter call returns.
- `backend/database/routes/checkin.py`, `models/events.py` (only what recording
  needs), plus new audit-specific adapter routes/models (new files — name them;
  they must not collide with 06's rescope)
- `frontend/src/views/CheckinView.vue`, `frontend/src/views/AdminEventView.vue`
  (hide/disable gated controls; an audit trail view for admin+superuser),
  `frontend/src/api/index.js`, `frontend/src/stores/session.js` only if
  capability plumbing needs it (it already reflects capabilities)
- `API.md`, `deploy/snapshot/scrub.sql` (audit rows hold names, emails, notes — personal data)
- tests under `tests/ticket-perms/` and `tests/e2e/ticket-perms/`

## Design notes

- The capability model, not role names: `roles.py` grants capabilities per
  role and the SPA reflects them. A new capability (the lead names it in the
  file's voice, e.g. `tickets`), granted to exactly `admin` and `superuser`,
  is one line + one blurb and survives role reshuffles — matching the file's
  stated philosophy. **Endpoint-level checks** (`require_capability`-style),
  never UI hiding alone.
- What landed already records (keep it, build on it): `reservations.source`
  + `added_by` (committee tickets), `reservation_checkins.checked_by` +
  `method` ∈ scan|manual|override (the door), per-booking confirmation email
  state. What's missing is the *unified, detailed* log — one table answering
  "who did what, to whom, when, with what detail" across door + admin +
  system.
- Audit log shape (discover local conventions first; this is intent, not a
  schema order): `id, at TIMESTAMPTZ, actor_user_id NULL for system,
  actor_name snapshot, actor_role snapshot, action e.g. checkin |
  checkin_override | checkin_undo | override_undo | ticket_added |
  ticket_cancelled | ticket_type_changed | booking_confirmed_manual |
  booking_confirmed_sync | confirmation_resent, reservation_id, event_id,
  detail JSONB (note, previous type, matched sale id, …)`. Snapshot the name
  and role at action time: a deleted or role-changed account must not blank
  history.
- Undo semantics (verify against the landed undo code): undo of an ordinary
  check-in stays whole-roster (a door person fixing their own mis-scan);
  undo of an *override* requires the new capability (it cancels an
  admin decision). Both audited.
- Audit-log visibility: the log itself is admin+superuser only (same set as
  the gated actions; it names who overrode what — not for the general roster).
  Per-event view in the admin area, paginated, newest first; a global view
  only if trivially cheap. 403 below admin, asserted.
- No retention purge for the log itself — it exists precisely to be durable.
  But it is personal data → `scrub.sql` must truncate/blank it for staging
  snapshots, alongside the check-in truncation that landed there.

## Acceptance criteria

1. Door override: an `events`-capable non-admin committee member scans and
   checks in normally (member and guest), but `override: true` on an
   unpaid/cancelled ticket returns 403 naming the capability; `admin` and
   `superuser` overrides succeed and write an audit row (actor, booking,
   event, time, method=override).
2. Committee-added tickets: non-admin `events`-capable committee create,
   cancel and ticket-type-change requests all 403; `admin`/`superuser`
   succeed and audit (actor, holder, ticket type, note). Member and guest
   holders behave identically. Previously-landed behaviour (source=committee,
   added_by, immediate confirmed) is otherwise unchanged — regression tests
   from the landed suites stay green.
3. Manual booking confirm (admin "Mark bought"-class) is gated the same way
   and audited; the automatic daily-sync confirm still runs unattended for
   everyone's bookings and audits as a *system* actor, distinguishable from
   every manual row.
4. Undo: ordinary check-in undo works whole-roster; undo of an override
   requires the capability; both write audit rows. Double-check-in stays
   idempotent and reported, not silent.
5. The audit log: per-event endpoint paginated newest-first; below
   admin+superuser → 403; rows carry actor name+role, action, target, time,
   detail; the admin UI shows it (Playwright: perform an override + an add,
   see both rows appear with the right actor labels).
6. Frontend follows the permissions: non-admin committee see no "Let in
   anyway" / "Add ticket" affordances (e2e assert); admin/superuser do; the
   People tab reflects the new capability in roles.py describe() output.
7. `scrub.sql` covers every new personal-data column/table (audit log
   included); a scrub run leaves staging clean; `verify.sql`'s scan finds
   nothing. Migrations expand-only.
8. `API.md` documents the new capability, the gated endpoints and the audit
   endpoints; every existing suite still collects/passes statically (pytest
   collect clean, all e2e specs `node --check`, `npm run build` clean — the
   build env has no Docker; the first CI run is the execution gate).

## Constraints

- No new Union API surface: nothing here may call eActivities, and the
  sync's own confirm path only *records*, never dials out.
- Never read `.env` / `backend.env` / `env/`; build against the compose test
  stack fixtures only.
- Comments explain why; commit messages `area: what changed`; never push
  (the human pushes). Migrations immutable once shipped.
- Frontend routes stay lazy chunks (the reconciliation's router pattern);
  new admin surfaces must not regress section 03's chunk continuity tests.