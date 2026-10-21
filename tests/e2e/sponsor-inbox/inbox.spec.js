// Regression for the admin-wide error from GET /admin/sponsors/inbox: a
// read-only inbox must not throw or require the ability to send newsletters.
// Uses the local compose test stack and its fixture admin; never live staging.
const { test, expect } = require('@playwright/test')

test('sponsor inbox loads without 500 or an admin-wide error across tabs', async ({ page }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the test admin session')
  const inboxRequest = page.waitForResponse((response) =>
    response.url().includes('/api/admin/sponsors/inbox') && response.request().method() === 'GET',
  )
  await page.goto('/admin')
  const inboxResponse = await inboxRequest
  expect(inboxResponse.status()).toBe(200)
  const inbox = await inboxResponse.json()
  for (const key of ['notes', 'marketing', 'requests', 'interest', 'listings']) {
    expect(inbox[key]).toBeInstanceOf(Array)
  }
  await expect(page.getByText('Internal Server Error', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Events', exact: true }).click()
  await expect(page.getByText('Internal Server Error', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Sponsors', exact: true }).click()
  await expect(page.getByText('Internal Server Error', { exact: true })).toHaveCount(0)
})
