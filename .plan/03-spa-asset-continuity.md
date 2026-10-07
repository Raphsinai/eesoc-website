# 03 — SPA asset continuity: old tabs keep working after a deploy

**Depends on:** 01 (test conventions). Parallel-safe with 02 (disjoint files: this section
owns `frontend/` build + `deploy/nginx/`, 02 owns `deploy/k8s/`).

## Goal

A browser tab opened before a deploy can still navigate every page after it: previously
built hashed JS/CSS chunks stay fetchable across deploys, `index.html` is never cached,
hashed assets are cached long-term, and a missing chunk degrades into a clean reload
instead of a broken page.

## Owns

- `frontend/` build pipeline: `Dockerfile`, `vite.config.*`, `package.json` (build
  scripts only)
- `deploy/nginx/` (the nginx config the frontend image uses — check where it is mounted
  from and own that copy)
- `frontend/src/main.js` / router bootstrap (the chunk-load-error recovery hook)
- an object-storage path for old chunks **only if** it needs no cluster-side setup; if it
  does, use the in-image approach below and record the cluster-side option as a blocked
  human step
- tests under `tests/e2e/spa-assets/`

## Design notes

- Preferred, no-cluster-access approach: the frontend image keeps the **previous N
  builds' hashed assets** (CI copies `dist/` from the last build(s) into the new image
  alongside the current one; the union of files, `index.html` always current). Old tabs
  fetch old chunks from the same origin. Tune N (a few days of deploys) with a comment
  saying why.
- nginx: `index.html` → `no-cache` (or no-store with ETag revalidation); `/assets/*`
  → `immutable, max-age=31536000`. Media already served off the volume — don't regress it.
- Recovery hook: on a failed dynamic-import/chunk fetch (router lazy-load), reload the
  page once (guard against a reload loop, e.g. a sessionStorage flag) so the tab picks up
  the new `index.html`.

## Acceptance criteria

1. Playwright test: load the site, capture the hashed asset URLs, rebuild/recreate the
   frontend container with a changed bundle, then in the **same page** navigate to a
   lazily-loaded route → it still works (old chunk still served).
2. Playwright test: delete the old chunk scenario — simulate a missing chunk (serve a
   bundle whose old URL 404s) → the page reloads cleanly to the current version instead
   of showing a dead route; no infinite reload loop.
3. curl tests: `index.html` response is not long-cacheable; a hashed asset response
   carries `Cache-Control: immutable` long max-age.
4. The existing smoke checks and the whole existing site still build and serve
   (`npm run build` clean, docker image builds).
5. If a Garage bucket for old chunks was considered and rejected for needing
   cluster-side keys, that is recorded as a blocked human step with exact instructions —
   not silently dropped.

## Constraints

- Nothing in `deploy/k8s/base/` (that is 02's); if the image approach requires a k8s
  change at all, coordinate via the planner instead of editing it.
- No environment-specific values anywhere shared.
