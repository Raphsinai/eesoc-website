# 08 — Events: admin workflow (duplicate, series, scheduled publish, cancel, feedback)

**Depends on:** 05 (event fields), 07 (rich event content must be duplicated too). 09
depends on this.

## Goal

The committee-side mechanics society events actually need: duplicate an event, create a
series, publish on a schedule, cancel or postpone visibly, and run a post-event feedback
form. (The email side of cancel/feedback — notice drafts in the queue — belongs to
section 09; this section makes the underlying states and forms exist.)

## Owns

- migrations `050`–`052` (status: cancelled/postponed + original date, publish-at
  timestamp, feedback form definition + responses, series grouping — expand-only)
- `backend/api/routes/admin.py` (the event admin endpoints) and/or the event routes —
  coordinate with what 05/07 left in place
- `backend/database/routes/events.py` + `models/events.py`
- the api's background scheduler where scheduled publishing must wake up (built on 02's
  restart-safe job machinery — a publish due while a pod restarts still happens, once,
  after the restart)
- `frontend/src/views/AdminEventView.vue`, `frontend/src/views/AdminView.vue` (event list
  affordances: duplicate, series, cancel/postpone, schedule), `frontend/src/views/EventView.vue`
  + `EventsView.vue` (cancelled/postponed marking), `frontend/src/views/FeedbackView.vue`
  (new), `frontend/src/router/index.js`, `frontend/src/api/index.js`
- `API.md`, `deploy/snapshot/scrub.sql` (feedback responses are personal data — free-text
  answers!)
- tests under `tests/admin-workflow/` and `tests/e2e/admin-workflow/`

## Features

- **Duplicate:** one click copies an event with all of its config — including everything
  05 and 07 added (questions come too if 06 has landed by merge time; if not, 06's tests
  cover their own duplication — state which you found) — as an unpublished draft with
  future date, without copying bookings/holds/notify-me/answers.
- **Series/recurring:** from a pattern (e.g. weekly N times, or a list of dates) create
  N unpublished occurrences; afterwards edit one occurrence alone or all (bulk edit
  applies shared fields; per-occurrence fields like date stay separate).
- **Scheduled publishing:** set a publish-at time; the event flips to published then;
  occurs exactly once even across a pod restart; a publish-at in the past publishes
  immediately.
- **Cancel / postpone:** an event can be cancelled or postponed (new date). The public
  site marks it unmistakably (banner/label in list + detail), bookings stay visible with
  their status, and the API exposes the state clearly. **No emails from this section** —
  09 wires the notice drafts.
- **Feedback form:** after an event, committee defines a small form (reuse 06's
  question types if they have landed — check at merge time; otherwise a minimal
  text/rating set of your own, documented), attendees submit once, committee sees
  aggregate results + individual answers (behind the `events` capability). The link
  going out to attendees is 09's job.

## Acceptance criteria

1. Duplicating an event yields a new unpublished event with identical config and zero
   bookings/notify-me rows (assert the copies' relations).
2. Creating a 3-occurrence weekly series makes 3 events; editing "all" changes shared
   fields on every occurrence; editing one changes only it.
3. A scheduled publish fires at its time (test with a short/fast-forwardable interval or
   an injectable clock); killing the api container just before and restarting after still
   publishes exactly once.
4. Cancelling an event marks it on list and detail endpoints and pages; postponing shows
   the new date with the postponed state; bookings still list for the member.
5. Feedback: an attendee submits once (second submission rejected), a non-attendee
   cannot submit, results visible only with the `events` capability, free-text answers
   never in public payloads.
6. Feedback responses are purged by the same retention window as 06's answers (or their
   own, documented) and are scrubbed for staging.
7. All existing admin event flows still work; `API.md` updated; migrations expand-only;
   `manifests` job green.

## Constraints

- The scheduler additions must not call the Union API or add eActivities traffic.
- New free-text personal data → scrub.sql additions (never weaken verify.sql).
