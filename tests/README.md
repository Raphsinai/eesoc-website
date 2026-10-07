# tests/ — the pytest and Playwright suites

The suites run against the compose **test stack**: the real stack with the
ICU bridge swapped for a stub and mail pointed at a sink, so tests exercise
real code without touching eActivities (rate-limited per source IP; a breach
bans the whole society) and without emailing anybody.

## Layout — who owns what

Each later section of the plan owns **exactly two directories**:

- `tests/<slug>/` for pytest,
- `tests/e2e/<slug>/` for Playwright specs.

CI discovers the whole tree — `python3 -m pytest tests` and
`npx playwright test --config frontend/playwright.config.js` pick up every
spec under it — so **no later section
edits the CI workflow, `tests/pytest.ini`, `tests/conftest.py` or
`frontend/playwright.config.js` to make its tests run.** If a genuinely new
fixture is needed, it belongs in `tests/conftest.py` (shared) with a comment
naming the section that needs it.

Never edit `tests/baseline/` or `tests/e2e/baseline/` from another section;
add to your own directory instead.

## Rules

- **Tests never read `.env` or `backend.env`.** They hold secrets and exist
  only on dev machines; CI copies the `.example` templates. Tests use the
  override defaults baked into `docker-compose.test.yml`, plus the
  `EESOC_API_BASE` / `EESOC_NOLOGIN_BASE` / `EESOC_MAILPIT_BASE` /
  `EESOC_UNION_STUB_BASE` env vars if a local stack is on other ports.
- The **union stub replaces the real ICU bridge** (`tests/union_stub/`). It
  serves canned fixtures and no `ICU_API_KEY` exists anywhere in the test
  environment — CI asserts this before running a single test. Never point the
  test stack at the real bridge.
- **Mail goes to mailpit** (`http://localhost:8025`). Nothing a test sends
  reaches a real mailbox.
- `api` (port 8080, via nginx — same origin as the browser) has
  `DEV_LOGIN_ENABLED=true`; `api-nologin` (port 8081) has it off, for the
  real request-code → verify-code flow.
- Generated artifacts (storage state, traces, reports) are gitignored:
  `tests/e2e/.auth/`, `test-results/`, `playwright-report/`, `blob-report/`,
  `playwright/.cache/`. See `.gitignore`. Nothing a run produces is
  committable, and storage state holds live sessions — never commit it.

## Running locally

**Stop the dev stack first** — the test stack publishes the same 8080 (and
5433 for postgres) as `docker compose up`, so the two cannot run at once:

```bash
docker compose down                  # dev stack, if running

docker compose -f docker-compose.yml -f docker-compose.test.yml -p eesoc-test \
  up -d --build postgres database union-stub mailpit api api-nologin frontend
```

The service list is explicit and excludes `union` — the real ICU bridge must
never start in a test run. Then, from the repo root:

```bash
pip install -r backend/api/requirements-dev.txt
python3 -m pytest tests -v

npm ci                                                  # repo root — see below
npx playwright install chromium                         # once per machine
npx playwright test --config frontend/playwright.config.js
```

The Playwright install lives in the **root** `package.json`, not the frontend
one: the specs sit in `tests/e2e/`, outside the frontend package, and Node
resolves `@playwright/test` by walking up from the spec file — an install
under `frontend/` is invisible to them. The config stays in
`frontend/playwright.config.js`; always invoke it from the repo root as above.
Tear the stack down (and wipe its postgres volume) when finished:

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml -p eesoc-test down -v
```

## In CI

`.github/workflows/deploy.yml` runs the `pytest` and `e2e` jobs on every push
and PR, and `build` needs them — tests must pass before any image is built.
Both jobs bring the test stack up with the same explicit service list, dump
`docker compose logs` on failure, and `down -v` so each run starts from a
fresh postgres volume (the `-p eesoc-test` project scopes the volume;
`down -v` wipes it).
