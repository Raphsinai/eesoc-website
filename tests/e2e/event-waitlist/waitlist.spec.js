// When a sold-out event has its waitlist enabled, show a working join action.
// Disabling new joins in the admin must immediately restore Sold out.
const { test, expect } = require('@playwright/test')
const tag = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const title = `Waitlist test ${tag}`
const slug = `waitlist-test-${tag}`
let eventId = null

async function creatorCsrf(request) {
  const res = await request.post('/api/auth/dev-login', {
    data: { email: `waitlist-creator-${tag}@example.org`, make_admin: true },
  })
  expect(res.ok()).toBeTruthy()
  return (await res.json()).csrf_token
}

test.beforeAll(async ({ request }) => {
  const csrf = await creatorCsrf(request)
  const created = await request.post('/api/admin/events', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      title, slug, category: 'Socials',
      starts_at: '2030-10-31T18:00:00Z',
      sales_open_at: '2026-10-01T12:00:00Z',
      published: true,
      ticket_types: [{ name: 'Standard', price_pence: 0, capacity: 1,
                       requires_purchase: false }],
    },
  })
  expect(created.ok()).toBeTruthy()
  const event = await created.json()
  eventId = event.id
  const taken = await request.post(`/api/events/${slug}/reserve`, {
    headers: { 'X-CSRF-Token': csrf },
    data: { ticket_type_id: event.ticket_types[0].id, quantity: 1 },
  })
  expect(taken.ok()).toBeTruthy()
})

test.afterAll(async ({ request }) => {
  if (!eventId) return
  const csrf = await creatorCsrf(request)
  await request.delete(`/api/admin/events/${eventId}`, { headers: { 'X-CSRF-Token': csrf } })
})

test('Waitlist badge and signup only when enabled', async ({ page, request }) => {
  test.skip(test.info().project.name !== 'admin', 'needs separate signed-in user and admin controls')
  await page.goto(`/events/${slug}`)
  await expect(page.getByTestId('event-waitlist')).toBeVisible()
  await expect(page.getByText('Waitlist', { exact: true })).toBeVisible()
  await page.getByTestId('join-waitlist').click()
  await expect(page.getByRole('status')).toContainText("You're on the waitlist")

  await page.goto('/admin')
  await page.getByRole('button', { name: 'Events', exact: true }).click()
  const card = page.locator('.ees-card--outline').filter({ hasText: title })
  const control = card.getByRole('checkbox', { name: `Allow waitlist for ${title}` })
  await expect(control).toBeChecked()
  await Promise.all([
    page.waitForResponse((res) => res.url().includes(`/admin/events/${eventId}/waitlist-enabled`) && res.status() === 200),
    control.uncheck(),
  ])
  const disabled = await request.get(`/api/events/${slug}`)
  expect((await disabled.json()).status).toBe('Sold out')
  await page.goto(`/events/${slug}`)
  await expect(page.getByTestId('event-waitlist')).toHaveCount(0)
  await expect(page.getByTestId('event-fully-booked')).toBeVisible()
  await page.goto('/admin')
  await page.getByRole('button', { name: 'Events', exact: true }).click()
  const restored = page.getByRole('checkbox', { name: `Allow waitlist for ${title}` })
  await Promise.all([
    page.waitForResponse((res) => res.url().includes(`/admin/events/${eventId}/waitlist-enabled`) && res.status() === 200),
    restored.check(),
  ])
  await page.goto(`/events/${slug}`)
  await expect(page.getByTestId('event-waitlist')).toBeVisible()
})
