# DECISIONS — assumptions recorded during autonomous build rounds

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
