# CODEBASE — eesoc-website (surveyed 2026-10-07 by intake)

The Imperial College EESoc website: public site (events, resources, sponsors, jobs, committee),
member portal (bookings, lockers), and an admin dashboard (events, sponsors, email, roles).
Live at https://eesoc.com (production) and https://eesoc.raphsinai.dev (staging).

## Stack
- Frontend: Vue 3.5 + vue-router 4 + Vite 7, plain JS (no TS), lucide icons. Own design system in
  `frontend/src/components/ds/` and `frontend/src/styles/design-system/`. Served by nginx, which proxies `/api`.
- Backend: Python FastAPI 0.141, split into three services:
  - `backend/api`: public API, **the only service that checks authorisation**. httpx, boto3 (S3/Garage uploads), Pillow.
  - `backend/database`: SQL adapter (asyncpg). The only thing that talks to Postgres. **No auth by design**: never expose it.
  - `backend/union`: bridge to ICU eActivities API, throttled + cached in-process.
- Postgres 16. Migrations: `backend/database/migrations/NNN_*.sql` (000–034), applied automatically on startup.
  **Migrations are immutable once shipped**: add a new file, never edit an old one.
- Object storage: self-hosted Garage (S3-compatible) in k8s; a local volume in compose.

## Layout
- `frontend/src/views/*View.vue`: pages (AdminView.vue is 2.4k lines). `src/api/index.js`: API client.
  `src/stores/`: session/status. `src/router/index.js`.
- `backend/api/routes/*.py`: endpoints (admin, auth, committee, email, events, lockers, resources, search, seo, sponsors).
  `services/`: announce, emailing, identity, maintenance, purchase, storage, sync. `utils/`: auth, roles, deps.
- `backend/database/routes/` + `models/`: adapter endpoints per domain.
- `API.md`: the live API contract (keep it updated). `deploy/README.md`: full deployment docs.
- `deploy/k8s/base` + `overlays/{staging,production}`; ArgoCD apps in `deploy/argocd/`.

## Run / test
- Run locally: `docker compose up --build` → http://localhost:8080 (API docs at `/api/docs`).
  Needs `.env` and `backend.env` (copy from the `.example` files; CI does exactly that).
- Dev login (local only, `ENVIRONMENT=development`): `POST /api/auth/dev-login {"email":..., "make_admin":true}`.
  With no `SMTP_HOST`, login codes go to `docker compose logs api`.
- Frontend only: `cd frontend && npm install && npm run dev` / `npm run build`.
- **Tests:** pytest suites in `tests/` and Playwright suites in `tests/e2e/`, both run against the compose
  test stack (`docker compose -f docker-compose.yml -f docker-compose.test.yml -p eesoc-test up -d --build
  postgres database union-stub mailpit api api-nologin frontend` — the stub replaces the real ICU bridge;
  the service list must never include `union`). Then `pip install -r backend/api/requirements-dev.txt` +
  `python3 -m pytest tests -v`, and from the repo root `npm ci` (the Playwright install is in the root
  package.json — the specs live in tests/e2e, outside the frontend package) +
  `npx playwright install --with-deps chromium` +
  `npx playwright test --config frontend/playwright.config.js` (stop the dev stack first — both publish
  :8080). See `tests/README.md` for the
  per-section layout rules (`tests/<slug>/`, `tests/e2e/<slug>/`; CI discovers the whole tree).
  CI (`.github/workflows/deploy.yml`) runs `pytest` and `e2e` jobs on the same stack and gates `build` on
  them, alongside the `test` smoke job (compose up, wait for `/api/health`, assert every migration applied,
  wait for resource seeding, curl smoke checks, assert `dev_login_enabled` defaults to False) and a
  `manifests` job rendering both kustomize overlays and checking staging/production don't overlap.
- Tooling present locally: docker, node 26, a Python venv in `env/` (gitignored).

## Deploy (read CLAUDE.md, it is strict)
- Push to `main` → CI builds images → pins SHA in `overlays/staging` → ArgoCD deploys staging.
  Pushing to main *is* deploying to staging; Raph allows that freely.
- Production only via `gh workflow run promote.yml`, only after Raph approves that specific change on staging.
  Never hand-edit `overlays/production/kustomization.yaml`; never kubectl against namespace `eesoc`.
- Nothing environment-specific in `deploy/k8s/base/`.
- Staging DB is a scrubbed copy of production, restored after every deploy. New columns holding
  email/CID/name/phone must be added to `deploy/snapshot/scrub.sql` (`verify.sql` fails the snapshot otherwise).

## External services and credentials (all human-held; agents must not read them)
- ICU eActivities API (`ICU_API_KEY`, `ICU_CENTRE_CODE`): **rate-limited per IP; a breach bans the whole
  society**. Nothing in a request path may call it. `union` pinned to 1 replica, `Recreate`. Staging has no key.
- SMTP: shared Imperial mailbox `eesocweb@ic.ac.uk` (login codes, transactional) + a publicity mailbox
  (`PUB_SMTP_*`, bulk mail, BCC in batches). Password force-reset yearly → site enters maintenance mode.
  Mail from staging is sent for real.
- `EMAIL_TOKEN_SECRET`, Postgres creds, Garage keys: k8s secrets.
- `.env`, `backend.env` exist locally and contain secrets: never read or print.

## Conventions
- Comments explain *why*, in full sentences; commit messages `area: what changed` (e.g. `email: ...`, `admin: ...`).
- Session cookie + CSRF header (`X-CSRF-Token`) on unsafe methods. Errors are `{"detail": ...}`.
- Roles: member / committee / sponsor; committee role is one of superuser | admin | events | industry,
  mapped to capabilities in `backend/api/utils/roles.py`.
- Booking flow: reserve → held → confirmed by matching a Union sale (daily sync 06:15) or expired/cancelled.

## Risks / tech debt
- No automated tests beyond smoke checks; regressions surface on staging or live.
- Very large files (AdminView.vue 2.4k lines, routes/admin.py 1.5k lines).
- `api` is pinned to 1 replica because the daily sync takes an in-process lock.
- `backend/README.md` is stale ("Not implemented yet"); API.md is the accurate doc.
- Repo root holds `.DS_Store`, a 9 MB committee-cards HTML (gitignored), and `sponsor_logos/` (tracked).
- No TODO/FIXME markers found in code.
