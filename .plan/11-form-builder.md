# 11 — Form builder: arbitrary committee-made forms

> **PROPOSED — awaiting approval** (request 02, round 2). Not buildable until approved.

**Depends on:** 06 (field types, answer storage, retention/purge and privacy machinery —
**reuse** its question machinery, do not invent a second field format). Mostly new
files; low overlap with the events routes.

## Goal

Committee can create standalone forms — surveys, interest sign-ups, merch orders,
feedback not tied to an event — with the same field types as the event sign-up
questions, share a public link, and collect/view/export responses.

## Owns

- migrations `060`–`061` (forms, fields, responses — expand-only)
- `backend/api/routes/forms.py` (new), `backend/database/routes/forms.py` +
  `backend/database/models/forms.py` (new)
- form builder + responses UI in the admin views, public `FormView.vue` (submit page),
  `frontend/src/router/index.js`, `frontend/src/api/index.js`, `API.md`
- `deploy/snapshot/scrub.sql` (responses hold names, emails, free text)
- tests under `tests/forms/` and `tests/e2e/forms/`

## Behaviour

- **Builder** (committee with the `events` capability): title, description, fields —
  short text, long text, single-choice, multi-choice, required/optional (06's types) —
  open/closed state, optional closing date.
- **Public page** at a shareable URL: renders the form; a signed-in member submits once
  (second submission rejected); optional per-form **guest mode** (name+email, like
  event guest booking), off by default.
- **Closing:** past the closing date or manually closed → submissions refused;
  committee views of responses remain.
- **Responses:** per-person list, per-choice aggregate counts, CSV export — visible
  only to the `events` capability (endpoint-level assert, not UI hiding); never in any
  public/member-facing payload.
- **Personal data:** retention purge (default 30 days after the form closes,
  configurable like 06's), `scrub.sql` columns, `verify.sql` untouched.
- **No email at all from this section.** No new queue triggers — a committee member
  pastes a form's link into an existing queue draft by hand if they want to publicise
  it.

## Acceptance criteria

1. Create a form with every field type; the public page renders it; required enforced
   (API rejects missing), optional saved; choices round-trip.
2. A member submits once; a second submission is rejected; guest mode off blocks guest
   submission, on captures name+email.
3. Closing (by date and by hand) refuses new submissions; closed forms stay viewable to
   committee.
4. Responses + aggregates + CSV visible only with the `events` capability (403
   otherwise); absence from public payloads asserted.
5. Retention purge deletes responses after the window; a scrub run leaves staging clean.
6. `API.md` updated; existing tests green; migrations expand-only; `manifests` job
   green.

## Constraints

- Reuse 06's field/question machinery — no second field format.
- No sends, no cron, no automatic anything.
- Never read `.env`/`backend.env`; comments explain why; commit style `area: what
  changed`.