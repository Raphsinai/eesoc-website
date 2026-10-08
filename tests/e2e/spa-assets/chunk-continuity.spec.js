// AC 1 — a tab opened before a deploy keeps working after it.
//
// WHY this rebuilds the frontend container mid-suite: the criterion is that an
// already-open tab, whose bundle references the old hashed chunk URLs, must be
// able to navigate after a deploy. The only way to make the *same* browser tab
// observe a deploy is to rebuild + recreate the frontend service under it and
// then assert the persistent page still functions. Two compose builds happen
// here (variant spa-cont-a, then spa-cont-b with spa-cont-a's assets unioned
// in); across both spa-assets specs that is four builds total, within the
// suite budget.
//
// WHY this is safe in CI: the e2e job runs Playwright with workers=1 (the
// default when CI=1), so no other spec executes while this one rebuilds the
// stack — a recreate can never race a concurrent spec's requests. Playwright
// also runs this spec in the `admin` project; the first line skips that, since
// the admin tab is byte-for-byte the same bundle as the public one and each
// rebuild is expensive. This spec runs last in file order (tests/e2e/spa-assets
// sorts after tests/e2e/baseline), so the final build variant left behind does
// not matter to any other spec — baseline asserts static chrome and API data,
// never data-build.
const { test, expect } = require('@playwright/test')
const { uniqueTag, tagCurrentFrontend, rebuildFrontend } = require('./compose')

// Two docker builds + a lazy navigation: well beyond the 30s Playwright
// default, so the whole file runs under a generous, bounded timeout.
test.setTimeout(20 * 60 * 1000)

const VARIANT_A = 'spa-cont-a'
const VARIANT_B = 'spa-cont-b'

test('a pre-deploy tab keeps working: old chunks still served, lazy route renders, no reload', async ({ page }) => {
  test.skip(test.info().project.name !== 'public', 'docker rebuilds are stateful and identical for an admin tab; run it once')

  // --- serve variant A, the "pre-deploy" bundle ---------------------------
  await rebuildFrontend({ variant: VARIANT_A })

  await page.goto('/')
  // The build variant is baked onto <html data-build> by main.js; this is how
  // we tell which bundle the live tab is running, before and after the deploy.
  await expect(page.locator('html')).toHaveAttribute('data-build', VARIANT_A)

  // Every hashed asset the entry shell references right now: the entry script,
  // its modulepreload links and the stylesheet. These are the URLs an old tab
  // holds in memory (and its cache) when the deploy lands.
  const assetPaths = await page.evaluate(() => {
    const seen = new Set()
    const collect = (node) => {
      const url = new URL(node.src || node.href, location.origin)
      if (url.origin === location.origin && url.pathname.startsWith('/assets/')) {
        seen.add(url.pathname)
      }
    }
    document.querySelectorAll('script[src]').forEach(collect)
    document.querySelectorAll('link[href]').forEach(collect)
    return [...seen]
  })
  expect(assetPaths.length, 'the built shell references hashed /assets/ URLs').toBeGreaterThan(0)

  // Preserve the variant-A image under a per-run tag BEFORE the next build
  // overwrites the `eesoc-website-frontend` label.
  const previousTag = uniqueTag(VARIANT_A)
  tagCurrentFrontend(previousTag)

  // --- "deploy": rebuild variant B, unioning variant A's assets into it ----
  await rebuildFrontend({ variant: VARIANT_B, previousImage: previousTag })

  // The deploy finished, but the tab in `page` is untouched — it still runs
  // variant A. Its entry-chunk URL no longer exists in variant B's own dist
  // (the variant is baked into the content hash), so only the unioned-in copy
  // can answer. Every URL this tab knows must still be served.
  for (const assetPath of assetPaths) {
    const response = await page.request.get(assetPath)
    expect(
      response.status(),
      `old asset URL must still answer 200 (union serving ${assetPath})`,
    ).toBe(200)
  }

  // Still the same tab with the same in-memory bundle: no reload has occurred.
  await expect(page.locator('html')).toHaveAttribute('data-build', VARIANT_A)

  // --- the same tab navigates lazily to a route it has never visited --------
  // 'Browse resources' pushes /resources (client-side, no page load). The old
  // bundle must fetch its old (variant-A) route chunk on demand; the union
  // serves it, the view mounts, and the tab never reloads.
  const chunkResponses = []
  page.on('response', (response) => {
    if (new URL(response.url()).pathname.startsWith('/assets/')) chunkResponses.push(response)
  })

  await page.getByRole('button', { name: 'Browse resources' }).click()
  await expect(page).toHaveURL(/\/resources$/)
  // The search input is static chrome rendered as soon as ResourcesView
  // mounts — proof of mounting that does not depend on an API fetch landing.
  await expect(page.getByPlaceholder('Search this page…')).toBeVisible()

  // The lazily-loaded chunk(s) fetched for this navigation answered 200 — the
  // union, not a 404. (With lazy routes this is a chunk fetch; if zero asset
  // requests fired the router is not yet split — see the section spec.)
  expect(chunkResponses.length, 'lazy navigation fetched the route chunk').toBeGreaterThan(0)
  for (const response of chunkResponses) {
    expect(response.status(), `lazy chunk ${response.url()} answered 200`).toBe(200)
  }

  // And throughout: this is still the pre-deploy bundle — no reload, no new
  // app shell. That is the whole acceptance criterion.
  await expect(page.locator('html')).toHaveAttribute('data-build', VARIANT_A)
})