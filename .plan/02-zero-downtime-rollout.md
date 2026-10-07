# 02 — Zero-downtime rollout: graceful shutdown, resumable jobs, migration guard

**Depends on:** 01 (its CI jobs and `tests/` conventions).

Read `.plan/CODEBASE.md`, `CLAUDE.md`, `deploy/README.md`, and the k8s manifests in
`deploy/k8s/base/` before starting. **Staging only** — base-manifest changes must be safe
for production to inherit later, and `overlays/production/` must not be touched.

## Goal

A pod being replaced (deploy, node churn, crash) never drops a request, never double-sends
an email, and never runs the daily Union sync twice. Migrations are provably
expand/contract compatible.

## Owns

- `deploy/k8s/base/*.yaml` (all of them: api, frontend, database, union, garage, postgres,
  ingress, configmap, networkpolicy, kustomization) — rollout strategy, preStop hooks,
  `terminationGracePeriodSeconds`, probes
- `deploy/README.md` — write down the expand/contract migration rule
- `scripts/` — new `scripts/watch-rollout.sh` (and anything it needs)
- `scripts/check-migrations.*` (new) — the destructive-migration CI check
- `.github/workflows/deploy.yml` — add the migration-check job and the rolling-restart
  CI test job (shared with 01; 01 has already landed)
- `backend/api/Dockerfile`, `backend/database/Dockerfile`, `backend/union/Dockerfile`
  (entrypoint/uvicorn flags), `backend/*/main.py` (shutdown handling only — coordinate:
  05+ own route logic in the same trees, so keep edits to startup/shutdown plumbing)
- `backend/api/services/sync.py` (single-run guard across pods)
- `backend/api/services/emailing.py` + `backend/api/services/announce.py` (resumable,
  claim-before-send bulk jobs) and the api's background-job scheduling (wherever the
  on-sale/reminder loops are launched from)
- `backend/api/services/storage.py` / union client call sites — retry-not-500 while union
  or Garage restarts
- migrations `035`–`039` (send-claim / job-state tables as needed)
- tests under `tests/zero-downtime/` and `tests/e2e/zero-downtime/`

## Design notes

- **api is currently 1 replica** because the daily sync takes an in-process lock. Moving
  the sync out of the api is out of scope, but a **database advisory lock** (claimed via
  the database adapter) lets two api pods coexist during a RollingUpdate
  (`maxUnavailable: 0`, `maxSurge: 1`) without either double-calling eActivities or
  skipping the sync. That is the intended approach; if it turns out to be impossible,
  stop and report — the brief allows raising "move the sync" as a plan change, it does
  not allow scaling `union` or leaving the api taking downtime.
- Bulk sends already track campaigns (`email_campaigns`, migration 029). Extend to
  per-recipient claimed/sent state in Postgres so a killed pod's work is resumed by the
  next pod: claim (mark) → send → confirm; never send to a recipient already marked sent.
  The claim must be visible across pods, so an in-process lock is not enough — use the
  database. Same guard pattern for announce/reminder jobs. At most one sender active at a
  time across old and new pods.
- Graceful shutdown: uvicorn `--timeout-graceful-shutdown`, SIGTERM handling, preStop
  (`sleep` 5–10s so endpoints are removed from Service load balancing first),
  `terminationGracePeriodSeconds` comfortably longer than preStop + drain. The frontend
  nginx container needs the same treatment (nginx -s quit via preStop exec).
- `union` and Garage stay `Recreate`, 1 replica. While they restart the api must serve
  events from its cache/mirror (check what `union` data the api already caches — union
  cache tables exist, migration 009) and uploads must return a retryable response, not 500.

## Acceptance criteria

1. `scripts/watch-rollout.sh <base-url>` polls `/`, `/api/health`, `/api/events` and one
   hashed asset chunk every ~1s and exits non-zero on any non-2xx or connection error.
   Usable by Raph during a real staging deploy.
2. CI job: bring the compose stack up, apply constant load (e.g. a loop of curls / a
   small load script), `docker compose up -d --build api` (recreate under load) →
   **zero** failed requests; the load script itself asserts this.
3. CI test: start a bulk send to the mailpit sink with enough recipients that it is still
   running, kill the api container mid-send, restart it → the send completes and
   **no recipient appears twice** in mailpit; the campaign's sent count is correct.
4. CI test: two api replicas never both run the Union sync (stub union counts requests;
   the count stays at what one run would issue).
5. CI job: `check-migrations` fails on a migration containing DROP COLUMN/TABLE, RENAME,
   or NOT NULL without DEFAULT, unless the file carries an explicit marker comment
   (e.g. `-- contract-step: <reason>`) that the check prints. It passes on all existing
   migrations 000–034 and fails on a deliberately-bad fixture file in the test.
6. `deploy/README.md` documents the expand/contract rule (expand migration → deploy →
   old code still works → contract migration in a later deploy) and the marker.
7. The `manifests` CI job stays green; nothing environment-specific entered
   `deploy/k8s/base/` (no hosts, image tags, or `namespace:` there).
8. While `union` (stub) is stopped, `/api/events` still returns 200 from cache and an
   upload endpoint returns a retryable error (documented status/body), not a 500.

## Human / blocked items to note in the report (do not do yourself)

- Real staging verification: push to `main` and run `scripts/watch-rollout.sh
   https://eesoc.raphsinai.dev` during the deploy — needs Raph if the build environment
  cannot push.
