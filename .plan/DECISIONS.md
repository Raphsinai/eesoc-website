# DECISIONS — assumptions recorded during autonomous build rounds
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
