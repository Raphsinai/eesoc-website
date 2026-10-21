// The admin can choose a qualitative label, which appears on public What's On.
// Uses the isolated development/Union-stub compose stack only.
const { test, expect } = require('@playwright/test')

const tag = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
const title = `Badge test ${tag}`
const slug = `badge-test-${tag}`
let eventId = null

async function adminCsrf(request) {
  const login = await request.post('/api/auth/dev-login', {
    data: { email: `badge-${tag}@example.org`, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

test.beforeAll(async ({ request }) => {
  const csrf = await adminCsrf(request)
  const event = await request.post('/api/admin/events', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      title, slug, category: 'Socials',
      starts_at: '2030-10-31T18:00:00Z',
      ends_at: '2030-10-31T22:00:00Z',
      sales_open_at: '2026-10-01T12:00:00Z',
      published: true,
      ticket_types: [{ name: 'Standard', price_pence: 1500, capacity: 12, requires_purchase: true }],
    },
  })
  expect(event.ok()).toBeTruthy()
  eventId = (await event.json()).id
})

test.afterAll(async ({ request }) => {
  if (eventId === null) return
  const csrf = await adminCsrf(request)
  await request.delete(`/api/admin/events/${eventId}`, { headers: { 'X-CSRF-Token': csrf } })
})

test('admin badge selector controls qualitative public listing without showing counts', async ({ page, request }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')
  await page.goto('/admin')
  await page.getByRole('button', { name: 'Events', exact: true }).click()
  const card = page.locator('.ees-card--outline').filter({ hasText: title })
  const selector = card.getByRole('combobox', { name: `What's On label for ${title}` })
  await expect(selector).toHaveValue('auto')

  const checkPublic = async (expected) => {
    const response = await request.get(`/api/events/${slug}`)
    expect(response.ok()).toBeTruthy()
    const data = await response.json()
    expect(data.status).toBe(expected)
    expect(data.status).not.toMatch(/\d+ places/)
  }
  await checkPublic('On sale')

  await Promise.all([
    page.waitForResponse((res) => res.url().includes(`/admin/events/${eventId}/availability-badge`) && res.status() === 200),
    selector.selectOption('not_many_left'),
  ])
  await expect(selector).toHaveValue('not_many_left')
  await checkPublic('Not many left')

  await page.goto('/events')
  const publicCard = page.locator('.ees-card--offset').filter({ hasText: title })
  await expect(publicCard).toContainText('Not many left')
  await expect(publicCard).not.toContainText(/\d+ places/)

  await page.goto('/admin')
  await page.getByRole('button', { name: 'Events', exact: true }).click()
  const selectAgain = page.getByRole('combobox', { name: `What's On label for ${title}` })
  await Promise.all([
    page.waitForResponse((res) => res.url().includes(`/admin/events/${eventId}/availability-badge`) && res.status() === 200),
    selectAgain.selectOption('selling_fast'),
  ])
  await checkPublic('Selling fast')
  await Promise.all([
    page.waitForResponse((res) => res.url().includes(`/admin/events/${eventId}/availability-badge`) && res.status() === 200),
    selectAgain.selectOption('auto'),
  ])
  await checkPublic('On sale')
})
