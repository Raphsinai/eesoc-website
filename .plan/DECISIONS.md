# DECISIONS — assumptions recorded during autonomous build rounds
- **2026-10-10 (build round, notes):** two notes triaged.
  (1) *Sale end datetimes + end-of-day hold deadlines* → **feature / scope change**: new
  event fields, a new reservation gate and a new deadline rule on the live booking path.
  Proposed as **section 17, awaiting approval, not built.** Reading taken (details in
  `requests/07-*`): two optional fields `reservations_close_at` (no reserving after) and
  `union_sales_close_at` (no hold deadline may pass it); an N-day hold ends 23:59:59
  Europe/London on booking-date + N−1, one day later if booked at/after 23:00 London;
  unset fields keep today's behaviour; unlinked (no Union product) holds keep "until the
  event starts". The note's first sentence could instead mean end of the *next* day for a
  1-day hold — flagged for Raph to settle when approving.
  (2) *Cancelled tickets informed by email* → **tweak inside the ticketing scope**, built
  this round as **section 18**: a durable per-booking cancellation email, sender mirrors
  `services/confirmations.py`, runs on the announcer's ten-minute loop; neutral copy (who
  cancelled isn't recorded until 12's audit log); historical cancellations and silent
  committee-added tickets are `skipped`. Follow-up, not done: once 12/07 have merged, add
  `cancellations.trigger()` after the two cancel routes for immediate delivery.
- **2026-10-10 (build round, migration numbers):** per the passkeys note, sections land
  past 065: **17 → 066, 18 → 067, 12 → 068–069** (was 061–062, never built), proposed
  11 → 070–071 (its spec still says 062–063 — collides; renumber when approved). The
  runner applies by set, not high-water mark (`migrate.py`), and `check-migrations.py` has
  no ordering rule, so 07's already-built 047–049 and 16's 064 still apply on databases
  that already have 065 — 07's lead checks they don't depend on anything numbered later on
  a fresh database.
- **2026-10-10 (build round, scheduling):** STATUS.md was two rounds stale (13/14 merged,
  15/16 built-unmerged, passkeys + CI fixes landed out-of-plan and pushed). Waves by file
  ownership: **W1** 15 (finish: commit untracked tests, merge main, validate) ∥ 16 (finish)
  ∥ 12 (build; told not to touch `services/confirmations.py`/`database/routes/events.py`
  while 16 owns them — audit sync-confirms via a DB trigger in its own migration or
  similar). **W2** 18 ∥ 07 (merge main into its built branch, re-validate).
- **2026-10-10 (passkeys, Raph's direct request — outside the numbered plan):** passkey
  sign-in, an optional "save a passkey" step after every code sign-in, `/account` to
  manage them, and committee-made **passkey setup links** for people our codes never
  reach (mostly sponsors). Took migration **065** (`065_passkeys.sql`; 064 is reserved by
  section 16 — sections 11/12 should renumber past 065 too). New capability
  `passkey_links` on `admin` + `superuser`; a link for an account holding any committee
  role additionally needs `users`, and the rule is re-checked when the link is used.
  New maintenance key `passkeys` (not email-dependent; forcing `login` forces it).
  Passkey tables are truncated in `scrub.sql` and must-be-empty in `verify.sql`.
  Also fixed on the way, because CI had been red since the round-4 push and nothing
  could reach staging: `requirements-dev.txt` now includes the api runtime (pytest
  stopped at collection, so **no pytest suite had ever run in CI**); the `api_nologin`
  base URL; three stale tests (doors-close data, the event-payload allowlist, a
  capacity-0 waitlist event the schema forbids); Playwright's `admin` storage-state
  path and `workers: 1` under CI; the recovery spec counting `replaceState` as a
  reload; and a **real bug in section 03** — BusyBox `cp -an` never merged the previous
  build's assets, so old chunks were dropped on every deploy.
- **2026-10-08 (section 14, doors close):** `events.doors_close_at` (migration 063) is carried
  by the event models/read payloads only — **section 08 (event duplication/series, not yet
  built) must copy `doors_close_at` when it duplicates an event**, or duplicated events would
  silently lose their doors-close cutoff from every email and the event page. Recorded here
  so 08's spec writer finds it without re-deriving the dependency.

- **2026-10-08 (round 4, Raph's direct instructions):** "push to prod, you can bypass" read
  as **his explicit approval to promote the reconciliation** (main = the plan line + the
  landed ticketing) to production after CI green — not something I can or will force past
  the permission layer: `git push` stays denied from this session (dontAsk auto-deny), so
  he runs the push and the `promote.yml` workflow himself (commands in STATUS.md
  blockers). "Carry on and change the perms…" read as the approval of a **new section 12**
  (ticketing perms + audit log, spec'd in `.plan/12-ticket-perms-audit.md`, request triage
  `requests/03-*`): the door override ("Let in anyway") and committee-added-ticket
  create/cancel/type-change/manual-confirm gate on a new `roles.py` capability granted to
  exactly `admin`+`superuser`; the door screen itself stays whole-roster; a unified audit
  log (who/what/which booking/when/detail, system actions incl. the sync's automatic
  confirms) supersedes spec 10's `events`-capability gating for adds — spec 10's core had
  already landed out-of-plan on the origin line (a43c4b5, promoted to production). The log
  is personal data → `scrub.sql`; migration numbers 061–062 (058–060 are the landed
  line's). 06's rescope sequences **after** 12 (both touch AdminEventView).
- **2026-10-08 (round 4, reconciliation):** `origin/main` had diverged with a parallel line
  (same day) that already built part of the plan: QR tickets + door check-in (34f0ac3, on
  staging), committee-added tickets + per-booking confirmation email (a43c4b5, **promoted
  to production**), the 26-27 sponsor agreement, admin account creation, and staging pins —
  all with **no test coverage** (origin has no `tests/` and its CI runs no pytest/e2e; the
  whole test infra is our line's section 01). That line reserved migration numbers 058–060,
  outside our pre-assigned 035–057, and its commit messages coordinate with our plan
  explicitly ("plan 06's copy of this table, numbered 045, must be dropped when that section
  merges"). Decision: reconcile by merging `origin/main` **into** our `main` (merge commit
  2582c63) rather than rebase/rebuild — our 30 commits carry the test + rollout
  infrastructure the landed work never had, and the landed work carries the features our
  spec 06 would duplicate. Conflicts (both additive wiring, no behaviour change):
  `backend/database/main.py` registers both new routers; `frontend/src/router/index.js`
  keeps route-level lazy chunks and adds the new routes (checkin, ticket, admin-sponsors)
  as lazy loaders. Static gates green on the merged tree: compileall clean, 61 tests
  collect, every e2e spec `node --check`s, `npm run build` clean (jsQR splits to its own
  on-demand chunk). `git push` remains permission-blocked here, so **the first CI run of
  pytest+e2e on the merged tree is still the true execution gate** — Raph must push.
  Section 06 gets rescoped against a gap analysis (what of its ACs the landed work already
  covers) before any new build starts; the landed checkins migration is 060, so rescoped 06
  must not duplicate it.
- **2026-10-08 (round 3, planner writes):** Edit/Write are denied to the planner session
  in this build environment (don't-ask mode auto-denies any tool that would prompt).
  `.plan/STATUS.md` and `DECISIONS.md` updates are therefore written by an implementer
  subagent from planner-supplied exact content; feature code stays in the feature-leads
  (whose sessions hold their own Write grants). Read tools, git (status/log/merge/…),
  pytest/compile/node/npm remain allowed to the planner, which keeps doing merges and
  static gates itself.
- **2026-10-08 (round 3, merges):** sections 03 (`plan-03-spa-assets`, fast-forward) and
  05 (`plan-05-events-calendar`, merge commit) merged onto local `main` after static PASS
  reports. 02's lead hit its turn limit with work uncommitted and was resumed via
  SendMessage to finish, validate, and commit. 07 spawned after 05 merged. 05's validator
  flagged `backend/api/routes/admin.py` `list_all_events` (`limit=500`) as a pre-existing
  defect — same class as its own feed cap (fixed with pagination + `starts_at, id`
  tie-break) — to be handed to 08's prompt, which owns the admin routes. A small
  `deploy.yml` conflict is expected at 02's merge (03 added the `PREVIOUS_FRONTEND_IMAGE`
  build-arg; 02 adds CI jobs).

- **2026-10-08 (round 2, notes):** both new notes read as **new features** outside the
  approved plan → new sections `10` (committee-added tickets: bookings created directly
  as confirmed, counting against allocation) and `11` (a standalone form builder reusing
  06's field machinery), **proposed, awaiting approval — not built**. Readings and
  rationale in `.plan/requests/01-*` and `02-*`. If Raph meant something narrower by
  "make forms" than a standalone builder (e.g. only the already-approved per-event
  questions from 06), say so and section 11 shrinks or drops.
- **2026-10-08 (round 2, leftovers):** the interrupted previous round left **uncommitted**
  WIP in two worktrees (05: ~332 insertions incl. migrations 040/041, ics.py, tests;
  03: ~82 insertions, missing its Playwright tests). Both were committed onto their
  `worktree-agent-*` branches as clearly-marked unvalidated `wip:` commits and handed to
  fresh feature-leads with merge-then-review instructions (untrusted until re-validated
  against spec). Section 02's worktree was clean — started fresh.

- **2026-10-07 (round 1, section 01):** Playwright devDependencies live in a **root**
  `package.json`, not `frontend/package.json` as the spec assumed. Spec files live under
  `tests/e2e/` (outside `frontend/`), where Node cannot resolve an install under
  `frontend/`. Frontend package files untouched. Playwright config stays at
  `frontend/playwright.config.js` (its `testDir` points at `tests/e2e/`).
- **2026-10-07 (round 1):** `git push` and the Docker daemon socket (colima) are both
  blocked in the build environment. Consequences: the suites cannot be *executed* against
  a live stack during build rounds — they are validated statically (collection, compile,
  config/lint checks, validator review) and the first CI run is the true gate. No
  staging deploys happen from build rounds. Both recorded as blockers in STATUS.md.
- **2026-10-07 (round 1):** `.env.example` shows spuriously as deleted/modified in
  sandboxed `git` commands (the seatbelt deny pattern `**/.env*` matches it, so git
  cannot stat the file). The blob in git is intact and no commit touches it. Ignore it;
  never "restore" it from a worktree report.
