// The committee admin page loads for the signed-in admin.
//
// This spec runs in the `admin` project, whose storage state was prepared by
// the setup project signing in via dev-login with make_admin: true. The route
// guard lets "committee" through, and AdminView renders its heading
// unconditionally while the tab contents load.
const { test, expect } = require('@playwright/test')

test('admin page loads for the signed-in admin', async ({ page }) => {
  // Every spec runs in both projects (see playwright.config.js): signed-out in
  // `public`, carrying the admin storage state in `admin`. Only `admin` has a
  // session to load this page with, so the `public` pass skips rather than
  // failing on a redirect it can never satisfy.
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')

  await page.goto('/admin')

  // The fixed page heading from AdminView.vue. If the storage state is missing
  // or the session is not committee, the router redirects to '/' and this
  // heading never appears.
  await expect(page.getByRole('heading', { name: 'Committee admin' })).toBeVisible()
})