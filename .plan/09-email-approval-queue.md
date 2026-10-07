# 09 — Email approval queue

**Depends on:** 02 (bulk-send path + claim machinery), 05 (target year groups, external
sign-up link), 06 (confirmation paths stay automatic), 08 (cancel/postpone/feedback
states). Everything event- and content-side must have landed; this section wires the
triggers and the queue on top.

## Goal

Anything worth telling people about creates a pre-written, pre-addressed **draft** in an
admin approval queue. A human reviews, edits, sends or dismisses. Nothing bulk ever
sends automatically again except the transactional/personal mail that already exists.

## Owns

- migrations `053`–`057` (queue drafts: subject, heading, body, trigger reference,
  recipient selection state, edited-by-hand flag, sent/dismissed audit — expand-only)
- `backend/api/routes/queue.py` (new) — queue list/detail/edit/send/dismiss/test-send
- `backend/api/services/drafts.py` (new) — draft creation on triggers, content
  rendering, staleness, refresh-if-unedited
- `backend/api/services/announce.py` — **remove the automatic topic-based on-sale
  email** (notify-me alerts, hold reminders, waitlist offers, booking confirmations
  stay automatic)
- `backend/api/services/emailing.py` — the queue's send path (publicity mailbox, BCC
  batches, SQL-side opt-outs, unsubscribe headers, MAIL_ALLOWLIST guard, 02's
  claim/resume behaviour)
- trigger hooks in `backend/api/routes/events.py` (publish, time/venue change,
  sales-open), `backend/api/routes/admin.py` (job posting, committee posting, sponsor
  made public, cancel/postpone, feedback link), `backend/api/routes/sponsors.py`,
  `backend/api/routes/committee.py` (new minimal committee-opening/election posting, if
  none exists)
- `frontend/src/views/QueueView.vue` (new admin view), `frontend/src/views/AdminView.vue`
  (queue link + pending count), `frontend/src/router/index.js`, `frontend/src/api/index.js`
- `API.md`, `deploy/snapshot/scrub.sql` if any new column holds emails/names (audit
  actor, recipient snapshots)
- tests under `tests/email-queue/` and `tests/e2e/email-queue/`

## Behaviour

- **List:** admin "Queue" view, pending drafts newest first, pending count on the admin
  dashboard. Only the `site` capability can see the queue at all (404/403 for others).
- **Draft detail:** subject, heading, editable body, **pre-selected recipients
  (editable)**, preview render, "send test to the test list", Send, Dismiss. Audit
  records who sent/dismissed, when, and the final recipient count.
- **Never auto-send.** A draft goes stale when its subject no longer makes sense (event
  started or cancelled, job closed/removed, posting deleted): stale drafts leave the
  active list but remain viewable. Sending a stale draft is refused or clearly warned —
  pick one, document it.
- **One draft per trigger.** Editing the source before sending updates the draft's
  content *unless* a human has hand-edited it (then the human edit wins; the draft shows
  it is out of sync).
- **Triggers → drafts** (recipients pre-selected):
  - event published → "New events" topic + category subscribers + department lists for
    the event's target year groups
  - tickets on sale → "Tickets on sale" topic + category subscribers + target year
    groups — **replacing the current automatic on-sale topic email**; per-event
    notify-me alerts remain automatic
  - event time/venue changed after publishing → everyone booked + notify-me
  - cancelled/postponed → everyone booked + notify-me
  - post-event feedback → attendees
  - sponsor job listing posted or reopened → "Jobs and internships" topic
  - committee opening or election posted → "Committee and elections" topic (build the
    minimal posting if none exists)
  - new sponsor made public → "EESoc newsletter" topic
- **Content:** event drafts carry the full markdown-rendered description, date/time,
  venue, ticket types + prices, and every relevant link — event page, Union purchase link
  via `services/purchase.py`'s existing resolution order, site booking link, external
  sign-up link if set, add-to-calendar. Job drafts: role, company, location, deadline,
  description, apply link. Others: the posting's text + site link.
- Department lists are pre-selected **only** via an event's target year groups.
- Staging's `MAIL_ALLOWLIST` guard applies to queue sends and test sends; opt-outs are
  applied in SQL on send (recipient count reflects them).

## Acceptance criteria

1. Each trigger above (published, sales-open, venue change, cancel, postpone, feedback,
   job posted/reopened, committee opening posted, sponsor made public) creates **exactly
   one** draft with the specified recipients pre-selected — trigger it twice, still one
   draft (updated, not duplicated, unless hand-edited).
2. The on-sale topic email no longer auto-sends (announce's path is removed; regression
   test asserts no automatic mail appears in the sink when sales open), while notify-me
   alerts, hold reminders, waitlist offers and booking confirmations still send
   automatically.
3. A `site`-capable user can list, preview, edit recipients and body, send a test to the
   test list (visible in the mail sink), Send, and Dismiss; a non-`site` committee user
   gets no queue (and no API access) — assert on API responses, not just UI.
4. Send uses the bulk path: BCC batching, opt-outs excluded (count reflects), unsubscribe
   headers present, send is restart-safe per 02 (kill mid-send → resumes, no duplicates).
5. Audit rows exist for send and dismiss with actor, timestamp, final recipient count.
6. Staleness: event-started and event-cancelled drafts leave the active list and remain
   viewable; a closed/removed job or deleted posting does the same.
7. Hand-editing a draft then editing the source: the human body wins and the draft is
   flagged out of sync; without a human edit the draft refreshes.
8. Event draft bodies contain the rendered full description plus the event-page,
   purchase (correct resolution order), booking, external sign-up and calendar links —
   assert each link's presence and shape.
9. No draft is ever sent without Send (a trigger with no human action produces zero
   mail in the sink after any wait the test allows).
10. Every new endpoint in `API.md`; scrub.sql covers any new personal-data columns;
    existing newsletter/announce sending, unsubscribe flows and smoke/manifests jobs stay
    green.

## Constraints

- Sending from the queue must go through the existing publicity/bulk path — no new mail
  plumbing, no second SMTP profile.
- Nothing auto-sends; no cron that sends. The queue only lists and records.
- Migrations expand-only; no secrets; comments explain why; `area: what changed` commits.

## Human / blocked items (report, don't do)

- Live staging verification of the queue with real mail to the allowlist, per the brief's
  Testing section — Raph.
