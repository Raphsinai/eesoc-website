// Signed-in Imperial students see the daily sync warning but no expanded
// order-number field until they explicitly ask for it.
const { test, expect } = require('@playwright/test')
const tag = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const title = `Paid booking fallback ${tag}`
const slug = `paid-fallback-${tag}`
let eventId = null

async function creatorCsrf(request) {
  const login = await request.post('/api/auth/dev-login', {
    data: { email: `creator-${tag}@example.org`, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

test.beforeAll(async ({ request }) => {
  const csrf = await creatorCsrf(request)
  const created = await request.post('/api/admin/events', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      title, slug, category: 'Socials',
      starts_at: '2030-10-31T18:00:00Z',
      ends_at: '2030-10-31T21:00:00Z',
      sales_open_at: '2026-10-01T12:00:00Z',
      published: true,
      ticket_types: [{ name: 'Paid ticket', price_pence: 1500, capacity: 10,
                       requires_purchase: true }],
    },
  })
  expect(created.ok()).toBeTruthy()
  eventId = (await created.json()).id
})

test.afterAll(async ({ request }) => {
  if (!eventId) return
  const csrf = await creatorCsrf(request)
  await request.delete(`/api/admin/events/${eventId}`, { headers: { 'X-CSRF-Token': csrf } })
})

test('after reserving, show 24-hour sync notice then expand the order form on request', async ({ page }) => {
  test.skip(test.info().project.name !== 'admin', 'needs signed-in Imperial test account')
  await page.goto(`/events/${slug}`)
  await page.getByRole('button', { name: 'Reserve', exact: true }).click()
  await expect(page.getByTestId('order-number-help')).toBeVisible()
  await expect(page.getByTestId('order-number-help')).toContainText('24 hours after purchase')
  await expect(page.getByTestId('order-number-form')).toHaveCount(0)
  const toggle = page.getByTestId('order-number-toggle')
  await expect(toggle).toContainText('Still unconfirmed after 24 hours?')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')

  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByTestId('order-number-form')).toBeVisible()
  await expect(page.getByPlaceholder('Order number')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Submit order number', exact: true })).toBeDisabled()
  await page.getByPlaceholder('Order number').fill('EXAMPLE-ORD-12345')
  await expect(page.getByRole('button', { name: 'Submit order number', exact: true })).toBeEnabled()
  await toggle.click()
  await expect(page.getByTestId('order-number-form')).toHaveCount(0)
})
