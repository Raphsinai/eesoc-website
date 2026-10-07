// The real sign-in-via-code flow through the UI, from "Log in" to signed in.
//
// The code is fetched from mailpit's JSON API (the same sink the pytest
// baseline checks), proving the browser and the api agree on the whole loop:
// request -> email -> code -> verify -> session.
const { test, expect } = require('@playwright/test')

// Every spec runs in both the `public` project (signed out) and the `admin`
// project (carrying the setup project's storage state). This spec signs in
// from scratch, so it opts out of any storage state — in the `admin` project
// the browser would otherwise start already signed in, with no "Log in"
// button to click.
test.use({ storageState: { cookies: [], origins: [] } })

// Shared with the pytest suites' mailpit fixture, so both cannot drift on
// what "the code in the email" means. mailpit is a separate compose
// service, published to the host at :8025.
const { fetchLatestCode } = require('../helpers/mailpit')

test('sign in via emailed code ends signed in', async ({ page }) => {
  // A fresh address per run avoids the per-address resend cooldown and the 5-per-
  // hour rate limit that a re-run within a short CI window would otherwise trip.
  const email = `e2e-signin-${Date.now()}@example.com`

  await page.goto('/')

  // Open the dialog from the header's Log in button.
  await page.getByRole('button', { name: 'Log in' }).click()
  const dialog = page.getByRole('dialog')

  // Step one: enter the address and request the code.
  await dialog.getByPlaceholder('abc123@ic.ac.uk').fill(email)
  await dialog.getByRole('button', { name: 'Email me a code' }).click()

  // Step two: the code field replaces the email field.
  await expect(dialog.getByPlaceholder('123456')).toBeVisible()

  // The code travels through mailpit, not an on-screen timer.
  const code = await fetchLatestCode({ to: email })
  await dialog.getByPlaceholder('123456').fill(code)

  // The submit button is "Log in", but the header still has one too -- scope
  // to the dialog so the click lands on the form.
  await dialog.getByRole('button', { name: 'Log in', exact: true }).click()

  // Signed in: the header swaps its Log in button for an identity + Log out.
  // (A brand-new external address may then be asked for a name, but the session
  // is open either way, which is what this test asserts.)
  await expect(page.getByRole('button', { name: 'Log out' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Log in' })).toHaveCount(0)
})