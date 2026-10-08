// Compose helpers for the SPA asset-continuity e2e specs (section 03).
//
// WHY this exists: the two specs (chunk-continuity, chunk-recovery) rehearse a
// deploy by rebuilding the `frontend` compose service mid-suite — once with a
// previous image unioned in (old chunks survive), once with an empty union
// (old chunks 404). Rebuilding means `docker compose build` + `up -d frontend`,
// which has to run from the repo root so the build context ./frontend and the
// env files resolve. This module is the one place that spells out that
// machinery; the specs stay readable as a narrative.
//
// CommonJS on purpose: everything under tests/e2e resolves against the
// repo-root package, which declares "type": "commonjs", so the specs require()
// this file exactly like they require('@playwright/test').
'use strict'

const { spawnSync } = require('node:child_process')
const { join } = require('node:path')

// Repo root — the compose files live there and `docker compose` must run from
// it so relative build contexts and env paths resolve. This file sits at
// tests/e2e/spa-assets/, so three hops up is the root.
const REPO_ROOT = join(__dirname, '..', '..', '..')

// The image tag the compose file prints the frontend build to (see the
// `image:` key of the frontend service in docker-compose.yml). The Dockerfile
// unions PREVIOUS_FRONTEND_IMAGE into each build; the image we just built is
// the "previous" one for the next build, so we tag it before rebuilding.
const FRONTEND_IMAGE = 'eesoc-website-frontend'

// Frontend origin, overridable the same way the pytest suites override their
// base URLs (EESOC_FRONTEND_BASE) so a local stack on other ports still works.
const FRONTEND_BASE = process.env.EESOC_FRONTEND_BASE || 'http://localhost:8080'

// The compose project name the test stack runs under; reusing it here keeps
// these commands hitting the right containers even when a dev stack is up
// under the default project name.
const COMPOSE_BASE = [
  '-f', 'docker-compose.yml',
  '-f', 'docker-compose.test.yml',
  '-p', 'eesoc-test',
]

// Vite builds (and a cold npm ci layer) take a minute or two; buildx-less
// `docker compose build` on a CI runner can take a few more. Kept generous so
// a slow shared runner does not flake — but bounded, so a hung build still
// fails the test instead of the job.
const BUILD_TIMEOUT_MS = 10 * 60 * 1000

// spawnSync option shared by every invocation: huge maxBuffer because a vite
// build prints a lot, and the largest output must not truncate mid-stream.
function run(dockerArgs, { timeoutMs = BUILD_TIMEOUT_MS } = {}) {
  const result = spawnSync(
    'docker',
    ['compose', ...COMPOSE_BASE, ...dockerArgs],
    {
      cwd: REPO_ROOT,
      timeout: timeoutMs,
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
    },
  )
  if (result.error) {
    // spawn failed outright (docker missing, killed) — the stack is unusable.
    throw new Error(`docker compose ${dockerArgs.join(' ')} could not run: ${result.error.message}`)
  }
  if (result.status !== 0) {
    const out = [result.stdout, result.stderr].filter(Boolean).join('\n').slice(-4000)
    throw new Error(`docker compose ${dockerArgs.join(' ')} exited ${result.status}:\n${out}`)
  }
  return result
}

// A per-run tag for the image we want to keep as "the previous build". Unique
// per run so repeated CI runs never reuse (or collide over) a leftover tag,
// and self-describing so `docker images` during a failure shows which build it
// belonged to.
function uniqueTag(variant) {
  return `${FRONTEND_IMAGE}:${variant}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

// Preserve the image compose currently labels `eesoc-website-frontend` under a
// per-run tag BEFORE the next build overwrites that label. The next build then
// passes this tag as PREVIOUS_FRONTEND_IMAGE so the old hashed assets are
// unioned into the new image (see frontend/Dockerfile).
function tagCurrentFrontend(tag) {
  const result = spawnSync('docker', ['tag', FRONTEND_IMAGE, tag], { encoding: 'utf8' })
  if (result.status !== 0) {
    throw new Error(`docker tag ${FRONTEND_IMAGE} ${tag} failed: ${result.stderr}`)
  }
}

// Poll GET / until nginx answers 200. A recreate takes a few seconds while
// nginx resolves `api` and binds, and during it the port refuses (or nginx
// 502s). Bounded, generous polling — never a bare fixed sleep.
async function waitForFrontend({ timeoutMs = 60000 } = {}) {
  const deadline = Date.now() + timeoutMs
  let lastError = null
  while (Date.now() < deadline) {
    try {
      const response = await fetch(FRONTEND_BASE + '/', { signal: AbortSignal.timeout(5000) })
      if (response.ok) return
      lastError = `HTTP ${response.status}`
    } catch (error) {
      lastError = error.message
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error(`frontend never answered GET / within ${timeoutMs}ms (last: ${lastError})`)
}

// Build the frontend image for `variant` (baked into every emitted hashed
// filename via EESOC_BUILD_VARIANT) and recreate the container from it.
//
// `previousImage` is optional: when set (a per-run tag produced by
// tagCurrentFrontend), the Dockerfile unions that image's /assets into this
// build, so old chunk URLs keep answering. When omitted, the Dockerfile's
// default (vanilla nginx) yields an empty union, so old asset URLs 404 — the
// "missing chunk" lever the recovery spec pulls.
async function rebuildFrontend({ variant, previousImage }) {
  const buildArgs = [`--build-arg`, `EESOC_BUILD_VARIANT=${variant}`]
  if (previousImage) {
    buildArgs.push('--build-arg', `PREVIOUS_FRONTEND_IMAGE=${previousImage}`)
  }
  run(['build', ...buildArgs, 'frontend'])
  // --force-recreate guarantees the recreated container serves the freshly
  // built image: compose's image-diff heuristic is usually right, but a test
  // whose entire point is "the container changed" must not rely on it.
  run(['up', '-d', '--force-recreate', 'frontend'])
  await waitForFrontend()
}

module.exports = {
  REPO_ROOT,
  FRONTEND_BASE,
  FRONTEND_IMAGE,
  uniqueTag,
  tagCurrentFrontend,
  rebuildFrontend,
  waitForFrontend,
}