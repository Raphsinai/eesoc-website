# 04 — Staging refresh with no downtime

**Depends on:** 02 (it changes `deploy/README.md` and the deploy/snapshot scripts should
be documented alongside the rollout rules).

Read `deploy/snapshot/` (snapshot.sh, restore.sh, scrub.sql, verify.sql, kustomization)
and find how the refresh is triggered after each deploy before designing. **Staging
only.** The scrub → verify → restore pipeline and its guarantees are sacred:
staging stays a scrubbed copy of production, refreshed after every deploy.

## Goal

The post-deploy restore of a production snapshot into staging does not take
eesoc.raphsinai.dev offline: the site keeps serving the old data until the new,
scrubbed copy is ready, then switches over.

## Owns

- `deploy/snapshot/*` (all scripts and its kustomization)
- the refresh trigger wherever it lives in git (find it: likely `deploy/argocd/`, a
  hook in `deploy/README.md`'s documented flow, or a job in the deploy workflow —
  **not** `deploy/k8s/base/` or `overlays/production/`)
- `deploy/README.md` — only the sections about the staging refresh (02 has landed its
  parts; merge cleanly, don't rewrite 02's content)
- tests under `tests/staging-refresh/` (compose-level: simulate refresh against the test
  stack's postgres)

## Design notes

- Likely shape: restore + scrub into a fresh schema (or database) on the staging
  postgres, run `verify.sql` against it, then switch the api's connection (or rename
  schemas / change a symlinked role) atomically, and only then drop the old copy. The
  site serves the previous scrubbed copy throughout.
- Anything that needs CNPG/database permissions a git commit cannot express (e.g.
  creating databases from a Job's service account) becomes a **blocked human step** with
  step-by-step instructions — build everything around it first.
- Never weaken scrub or verify. New columns from other sections are being added to
  scrub.sql by those sections; if the refresh redesign needs to see them, take them as
  they land (this section merges after them in dependency order only if needed —
  otherwise note the coordination).

## Acceptance criteria

1. Compose-level CI test: while the api is serving, run the new refresh procedure
   against a "production-like" postgres (seeded, with deliberately real-looking data) →
   `/api/health` and `/api/events` never fail during the refresh, and after the switchover
   the api serves the new data.
2. The scrubbed result still passes `verify.sql` — zero real-looking personal data
   survives (existing assertions unchanged).
3. A failed refresh (inject an error mid-restore) leaves staging serving the **old**
   data, and the failure is loud (job/step fails), not a half-restored live database.
4. Refresh is idempotent and safe to re-run.
5. `deploy/README.md` documents the new refresh flow and its guarantees.
6. `manifests` CI job stays green; `overlays/production/` untouched; nothing
   environment-specific in `deploy/k8s/base/`.
