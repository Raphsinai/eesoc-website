// An unsold Union/SUMS shop product is linkable even when it is absent from
// eActivities' synced product dropdown. Uses the compose Union stub, never ICU.
const { test, expect } = require('@playwright/test')

const STAMP = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const EVENT = { title: `Manual SUMS product ${STAMP}`, slug: `manual-sums-${STAMP}` }
let eventId = null
let ticketId = null

async function login(request) {
  const res = await request.post('/api/auth/dev-login', {
    data: { email: `manual-sums-${STAMP}@example.org`, make_admin: true },
  })
  expect(res.ok()).toBeTruthy()
  return (await res.json()).csrf_token
}

test.beforeAll(async ({ request }) => {
  const csrf = await login(request)
  const made = await request.post('/api/admin/events', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      ...EVENT,
      category: 'Socials',
      venue: 'EEE 403',
      starts_at: '2030-10-31T18:00:00Z',
      ends_at: '2030-10-31T23:00:00Z',
      ticket_types: [{ name: 'Early Bird - Non-Alcoholic', price_pence: 1250,
                       capacity: 12, requires_purchase: true }],
    },
  })
  expect(made.ok()).toBeTruthy()
  const event = await made.json()
  eventId = event.id
  ticketId = event.ticket_types[0].id
})

test.afterAll(async ({ request }) => {
  if (!eventId) return
  const csrf = await login(request)
  await request.delete(`/api/admin/events/${eventId}`, { headers: { 'X-CSRF-Token': csrf } })
})

function labelField(page, label) {
  return page.locator('.ees-field').filter({
    has: page.locator('label.ees-label', { hasText: new RegExp(`^${label}$`) }),
  }).last()
}

async function openEditor(page) {
  await page.goto('/admin')
  await page.getByRole('button', { name: 'Events', exact: true }).click()
  const card = page.locator('div.card, [class*="card"]').filter({ hasText: EVENT.title })
  await card.getByRole('button', { name: 'Edit', exact: true }).last().click()
}

test('link a ticket via SUMS id without a synced product, retain it on reload, reject invalid edits', async ({ page, request }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')
  await openEditor(page)

  await labelField(page, 'Link using').locator('select').selectOption('manual')
  await labelField(page, 'SUMS product ID or shop URL').locator('input').fill('3043')
  await page.getByRole('button', { name: 'Link', exact: true }).last().click()
  await expect(page.getByText('Early Bird - Non-Alcoholic linked to its product.')).toBeVisible()

  const getLink = async () => {
    const rows = await (await request.get(`/api/admin/product-links?event_id=${eventId}`)).json()
    return rows.find((row) => row.ticket_type_id === ticketId)
  }
  expect(await getLink()).toMatchObject({ union_product_id: 1003043, union_product_line_id: null })

  await openEditor(page)
  await expect(labelField(page, 'Link using').locator('select')).toHaveValue('manual')
  await expect(labelField(page, 'SUMS product ID or shop URL').locator('input')).toHaveValue('3043')
  // Malformed manual links fail rather than removing the previous mapping.
  await labelField(page, 'SUMS product ID or shop URL').locator('input').fill('https://evil.example/shop/product/3043-/')
  await page.getByRole('button', { name: 'Link', exact: true }).last().click()
  await expect(page.getByText(/Enter a SUMS product ID or an Imperial College Union shop URL/)).toBeVisible()
  expect(await getLink()).toMatchObject({ union_product_id: 1003043, union_product_line_id: null })
})
