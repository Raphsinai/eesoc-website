# Brief: Zero-downtime deploys, richer events, and an email approval queue

Change to an existing codebase. Read `.plan/CODEBASE.md` first: stack, how to run, deploy rules, hazards.
The project `CLAUDE.md` deploy rules apply in full. **Target is staging only. Nothing is promoted to production.**

## Goal
1. Pushing a change never takes the site down, on staging or production, for visitors or for work in progress.
2. Events become as complete as a society needs: calendar, sign-up forms and check-in, rich pages, and admin workflow.
3. Anything worth telling people about creates a **draft email in an approval queue**. The draft is pre-written, with
   recipients pre-selected, and a committee member reviews, edits, and sends it or dismisses it.

## What it must do

### A. Zero downtime (both environments)
- Graceful shutdown on every service: a preStop delay so the endpoint is removed before the process stops, uvicorn
  drains in-flight requests, and `terminationGracePeriodSeconds` is long enough for both. nginx (`web` container) also
  drains.
- Bulk email sends and announce/reminder jobs survive a pod restart. A send interrupted mid-way resumes on the new pod
  from where it stopped. It must never re-send to people already sent to (keep the "claim before send" rule).
  At most one sender runs at a time across old and new pods during a rollout.
- Old browser tabs keep working after a deploy. Previously built JS/CSS chunks stay fetchable for a while
  (e.g. kept in the image or object storage). If a chunk is still missing, the SPA reloads cleanly instead of breaking.
  `index.html` is not cached; hashed assets are cached long-term.
- Rollout ordering is safe: a new migration must work with both the old and the new api running
  (expand/contract). Write this rule down in `deploy/README.md` and add a CI check that flags destructive migrations
  (DROP/RENAME column or table, NOT NULL without default) unless explicitly marked as a contract step.
- `union` and Garage use `Recreate` for good reasons (rate limit / single writer). Keep that, but the site must keep
  serving while they restart: the api serves from cache/local mirror, and uploads show a retry, not a 500.
  **Do not scale `union`.**
- Staging's post-deploy refresh-from-production must not take staging offline. For example, restore into a new schema
  or database and switch over atomically, or keep serving the old data until the restore finishes. The rule that
  staging is a scrubbed copy, refreshed after every deploy, stays.
- A rollout check: a script (`scripts/watch-rollout.sh` or similar) polls key URLs (`/`, `/api/health`, `/api/events`,
  an asset chunk) throughout a deploy and fails on any non-2xx or connection error. Also add an equivalent CI test
  that runs a compose-level rolling restart of the api under load with zero failed requests.

### B. Events
Keep everything that exists (tickets, holds, waitlist, guest booking, sales-open, product links, notify-me, sponsor
claims, categories). Add:
- **Calendar and links:** add-to-calendar (.ics download and Google Calendar link) on each event; a subscribable
  calendar feed for all public events (and per category); a map link for the venue; an online-meeting link (shown
  only to booked attendees if marked private); an **external sign-up link** for events that don't use Union tickets.
- **Sign-up forms and check-in:** per-event custom questions on booking (text, single/multi choice, required or
  optional; e.g. dietary, T-shirt size, year). Attendee CSV export including answers. A QR code ticket in the booking
  confirmation email and on "my bookings". A mobile-friendly door check-in screen for committee: scan the QR or search by
  name, mark attended, show the live count. This works for members and guests.
- **Rich event pages:** agenda/schedule items, speakers (name, title, photo, bio), linked sponsors, co-hosting
  societies, and after the event a photo gallery and slides/resources. A past-events archive with filtering by
  category, year and text search.
- **Admin workflow:** duplicate an event; recurring/series events (create N occurrences from a pattern, edit one or
  all); scheduled publishing (publish at a set time); cancel or postpone an event, which marks it clearly on the site
  and creates a notice draft in the queue for everyone booked or interested; a post-event feedback form, with the
  link sent to attendees via a queued draft and results shown to committee.
- **Target year groups:** a per-event setting. The matching department lists are pre-selected on that event's
  drafts.

### C. Email approval queue
- A new admin "Queue" view lists pending drafts (newest first), with a pending count shown on the admin dashboard.
- Each draft has a subject, heading, and editable body; **pre-selected recipients** (editable before sending); a
  preview; a "send test to the test list" button; and **Send** or **Dismiss**. Every action is recorded: who
  approved or dismissed, when, and the final recipient count. Sending uses the existing bulk path (publicity mailbox,
  BCC batches, opt-outs applied in SQL, unsubscribe headers).
- **Who can send:** anyone with the existing email-sending capability (`site`). Others can't see the queue.
- **Nothing in the queue is ever sent without a human pressing Send.** A draft becomes **stale** when it no longer
  makes sense (the event has started or been cancelled, the job is closed or removed, the posting was deleted). Stale
  drafts drop out of the active list and can still be viewed.
- **Triggers that create a draft** (one draft per trigger; editing the source before sending updates the draft unless
  someone has already edited it by hand):
  - Event published → recipients pre-selected: "New events" topic + that category's subscribers + department lists for
    the event's target year groups.
  - Event tickets go on sale → "Tickets on sale" topic + category subscribers + target year groups. **This replaces the
    current automatic topic-based on-sale email.** Personal "notify me" alerts for that event, hold reminders, waitlist
    offers and booking confirmations stay **automatic** (they are transactional and asked for).
  - Event time/venue changed (after publishing) → everyone booked + people with notify-me.
  - Event cancelled or postponed → everyone booked + notify-me.
  - Post-event feedback → attendees.
  - Sponsor job listing posted or reopened → "Jobs and internships" topic.
  - Committee opening or election posted → "Committee and elections" topic. Build a minimal way to post one if none
    exists.
  - New sponsor added (made public) → "EESoc newsletter" topic.
- **Draft content:**
  - Event drafts contain the **full event description** in the body (rendered from its markdown), plus date/time,
    venue, ticket types and prices, and **every relevant link**: the event page, the Union purchase link (using the
    existing resolution order in `services/purchase.py`), the site booking link, the external sign-up link if set, and
    add-to-calendar.
  - Job drafts contain the role, company, location, deadline, description and apply link.
  - Other drafts contain the posting's text and a link to it on the site.
- Department lists are never pre-selected except through an event's target year groups.

## Constraints and rules
- **Staging only.** Never run the promote workflow, edit `overlays/production/kustomization.yaml`, or kubectl the
  `eesoc` namespace. Production manifests may change only via the shared base, and those changes must be safe for
  production when Raph later promotes.
- Nothing environment-specific in `deploy/k8s/base/`. The CI `manifests` job must stay green.
- **eActivities:** nothing new calls the Union API from a request path, and nothing increases its request rate.
  `union` stays at one replica with Recreate. No `ICU_API_KEY` on staging.
- Email: staging's `MAIL_ALLOWLIST` guard must keep working for every new send path (queue, QR confirmations,
  feedback, notices). Opt-outs and unsubscribe links apply to every bulk send.
- Personal data: sign-up answers are visible only to people with the `events` capability, and are deleted
  automatically 30 days after the event (configurable). Every new column holding names, emails, CIDs, phone numbers or
  free-text answers is added to `deploy/snapshot/scrub.sql`. Never loosen `verify.sql`.
- Check-in QR codes are unguessable signed tokens, not booking ids.
- The calendar feed exposes only published public events, with no attendee data and no private meeting links.
- Migrations: only new files (never edit shipped ones), expand/contract compatible per section A.
- No secrets in the repo; never read `.env`, `backend.env` or k8s secrets.
- Keep the existing conventions: comments explain *why*, commit messages look like `area: what changed`, and
  `API.md` is updated for every new or changed endpoint.

## Testing
- Section 01 sets up a real test suite and wires it into CI: pytest for the api (and database adapter) against the
  compose stack, and Playwright browser tests for key flows. Mail goes to a fake SMTP sink (e.g. mailpit or an
  in-process capture). eActivities is replaced by fixtures/a stub `union`. Use the dev login only in the test stack.
- Every section adds tests for its own acceptance criteria. CI must stay green, including the existing smoke and
  manifest jobs.
- Zero downtime is tested in CI with a compose-level rolling restart under load (no failed requests, interrupted bulk
  send resumes with no duplicates), and on staging with the rollout-watch script during a real deploy.
- **Raph verifies live on staging:** the queue end to end with real mail to the allowlist, the QR check-in on a phone,
  and a push during which the site stays up.

## Human steps
- If deploying to staging (pushing to `main`) or watching the rollout on eesoc.raphsinai.dev isn't possible from the
  build environment, stop as `blocked` and ask Raph to push and run the rollout-watch script, with the exact
  commands.
- If any change needs cluster-side setup outside git (a new k8s secret, a Garage bucket/key for old asset chunks,
  CNPG/database permission for the staging swap-over), stop as `blocked` with step-by-step instructions. Build
  everything else first.
- Before calling it done: Raph checks the staging items listed under Testing.

## Out of scope
- Promoting anything to production.
- Changing the stack (Vue/FastAPI/Postgres/k8s/ArgoCD).
- Paying through the site. Payment stays on the Union shop.
- Scaling `union`; moving the daily ICU sync out of the api (unless zero downtime strictly needs it; if so, raise it
  as a plan change).
- Auto-sending queued drafts.
- Pre-filling queue drafts for resources or lockers (only the triggers listed above).

## Done means
- CI runs pytest and Playwright suites, plus the existing smoke and manifest jobs, all green.
- A CI rolling-restart test shows 0 failed requests during an api rollout, and an interrupted bulk send finishes
  after the restart with no recipient mailed twice.
- An old tab loaded before a deploy can still navigate to every page after it.
- A destructive migration without a contract marker fails CI.
- A push to `main` produces 0 failed requests in the rollout-watch script against staging, including during the
  staging data refresh.
- Each event feature in B works on staging. There is a test per feature: .ics/feed valid, custom questions saved and
  exported, QR check-in marks attendance, agenda/speakers/gallery render, archive filters, duplicate/recurring/
  scheduled publish/cancel/feedback all work.
- Publishing an event, opening its sales, changing its venue, cancelling it, posting a job, posting a committee
  opening, and adding a sponsor each create exactly one queue draft with the specified recipients pre-selected.
  The event drafts contain the full description and purchase, booking, sign-up and calendar links.
- No draft sends without Send. Dismiss works. Stale drafts leave the active list. Only `site` users can see the
  queue or send from it.
- The topic "tickets on sale" email no longer auto-sends. Notify-me alerts, reminders, waitlist offers and
  confirmations still do.
- Sign-up answers are purged after the retention window, and are absent from the staging snapshot.
- Staging runs all of the above. Production is untouched (the production overlay is unchanged).

## Must not break
Sign-in (code flow + maintenance mode), booking/holds/sale matching, lockers, sponsors portal, resources, committee
cards, existing newsletter and event-announce sending, unsubscribe flows, the staging scrub/verify, and the
`manifests` CI guarantees.
