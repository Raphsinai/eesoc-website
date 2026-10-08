// AC 2 — a missing chunk after a deploy degrades into one clean reload, no loop.
//
// WHY this rebuilds the frontend container mid-suite: the criterion is that a
// tab whose old chunk URLs have started 404ing must reload itself exactly once
// onto the current bundle instead of dying on a dead route. That 404 only
// exists after a deploy that does NOT keep the old assets, so this spec builds
// variant spa-rec-a, opens a tab, then rebuilds variant spa-rec-b with NO
// PREVIOUS_FRONTEND_IMAGE (empty union → every old hashed URL now 404s) and
// drives the still-open tab into the failure.
//
// WHY this is safe in CI: workers=1 (Playwright's CI default) means no other
// spec runs concurrently, so the recreate cannot race another spec's requests.
// A second compose build here (plus two in chunk-continuity) stays within the
// suite's build budget. The `admin` project gets skipped — the bundle is
// identical and each rebuild is expensive. Line `page.mainFrame()` filter
// ensures only real page navigations (not iframe noise) count toward the loop
// guard.
const { test, expect } = require('@playwright/test')
const { rebuildFrontend } = require('./compose')

// Two docker builds + a recovery reload + a quiet window: far beyond the 30s
// default.
test.setTimeout(20 * 60 * 1000)

const VARIANT_A = 'spa-rec-a'
const VARIANT_B = 'spa-rec-b'

test('a missing old chunk reloads once to the current bundle instead of looping', async ({ page }) => {
  test.skip(test.info().project.name !== 'public', 'docker rebuilds are stateful and identical for an admin tab; run it once')

  // --- serve variant A: the pre-deploy bundle this tab will keep running ----
  await rebuildFrontend({ variant: VARIANT_A })

  await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('data-build', VARIANT_A)

  // --- "deploy" without the previous build: old chunk URLs now 404 ----------
  await rebuildFrontend({ variant: VARIANT_B })

  // Count main-frame navigations from this moment on. The recovery hook
  // (frontend/src/chunk-recovery.js) must produce exactly one of these — the
  // location.reload() that fetches the new index.html.
  const navigations = []
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) navigations.push(frame.url())
  })

  // Click a CTA to a route this tab has NEVER visited. The old bundle lazily
  // imports its old chunk URL, which the new container 404s, so the recovery
  // hook reloads the page. The click itself may resolve before or after the
  // reload lands (the reload tears down the execution context); either is the
  // expected outcome, so swallow and assert on the result below.
  await page.getByRole('button', { name: 'Browse resources' }).click().catch(() => {})

  // The reload fetched the new no-cache index.html, so the tab now runs
  // variant B. waitForFunction polls the dataset until the new shell lands.
  await page.waitForFunction(
    (variant) => document.documentElement.dataset.build === variant,
    VARIANT_B,
    { timeout: 30000 },
  )
  await expect(page.locator('html')).toHaveAttribute('data-build', VARIANT_B)

  // Exactly one main-frame navigation so far: the recovery reload — not zero
  // (the tab would be stuck on the old bundle) and not several (a loop).
  expect(navigations.length, 'exactly one recovery reload').toBe(1)

  // The reloaded tab is a working app, not a dead route.
  await expect(page.getByRole('heading', { name: /Build things/ })).toBeVisible()

  // --- no infinite reload loop -----------------------------------------------
  // The recovery hook rate-limits to one reload per 10s, but the real guard
  // against a loop is that the new bundle's chunks exist: after settling on B,
  // the tab must go quiet. Poll the navigation count over ~3.5s and fail if a
  // second navigation sneaks in — a bounded poll, not a blind sleep.
  const settled = navigations.length
  for (let i = 0; i < 7; i++) {
    await page.waitForTimeout(500)
    expect(navigations.length, 'tab stays quiet after the single recovery reload (no loop)').toBe(settled)
  }
  await expect(page.locator('html')).toHaveAttribute('data-build', VARIANT_B)

  // --- the same CTA now works on the current bundle --------------------------
  // On variant B the route chunk exists, so this navigation must succeed
  // client-side — no reload (count unchanged), and the lazy route mounts.
  await page.getByRole('button', { name: 'Browse resources' }).click()
  await expect(page).toHaveURL(/\/resources$/)
  await expect(page.getByPlaceholder('Search this page…')).toBeVisible()
  expect(navigations.length, 'navigation on the recovered bundle causes no further reload').toBe(settled)
  await expect(page.locator('html')).toHaveAttribute('data-build', VARIANT_B)
})