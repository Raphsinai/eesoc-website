// Sale windows (section 17, criterion 8): the committee sets when booking
// stops and when the Union shop stops selling, and how many days a hold lasts,
// in the event editor; the event page says when booking closes, and once it
// has, offers no way to book.
//
// NEEDS THE COMPOSE TEST STACK — runs in CI only (see tests/README.md). Events
// are created through the admin write path in beforeAll.
const { test, expect } = require('@playwright/test')

const STAMP = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const SETUP_EMAIL = `windows-setup-${STAMP}@example.com`
const OPEN = { slug: `e2e-window-open-${STAMP}`, title: `E2E window open ${STAMP}` }
const SHUT = { slug: `e2e-window-shut-${STAMP}`, title: `E2E window shut ${STAMP}` }
// A May evening, away from any clock change, as the doors-close spec does.
const STARTS_AT = '2030-05-10T19:00:00.000Z'
const RESERVE_CLOSE_INPUT = '2030-05-08T18:00'
const UNION_CLOSE_INPUT = '2030-05-09T18:00'

const ids = {}

async function signInAsAdmin(request) {
  const login = await request.post('/api/auth/dev-login', {
    data: { email: SETUP_EMAIL, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

function eventBody(e, extra = {}) {
  return {
    slug: e.slug,
    title: e.title,
    category: 'Socials',
    venue: 'EEE 403',
    starts_at: STARTS_AT,
    ends_at: '2030-05-10T22:00:00.000Z',
    sales_open_at: new Date(Date.now() - 24 * 3600 * 1000).toISOString(),
    published: true,
    ticket_types: [{ name: 'Paid seat', price_pence: 800, capacity: 20, requires_purchase: true }],
    ...extra,
  }
}

test.beforeAll(async ({ request }) => {
  const csrf = await signInAsAdmin(request)
  const headers = { 'X-CSRF-Token': csrf }
  const open = await request.post('/api/admin/events', { data: eventBody(OPEN), headers })
  expect(open.ok()).toBeTruthy()
  ids.open = (await open.json()).id
  const shut = await request.post('/api/admin/events', {
    data: eventBody(SHUT, { reservations_close_at: new Date(Date.now() - 60 * 1000).toISOString() }),
    headers,
  })
  expect(shut.ok()).toBeTruthy()
  ids.shut = (await shut.json()).id
})

test.afterAll(async ({ request }) => {
  const csrf = await signInAsAdmin(request)
  for (const id of Object.values(ids)) {
    await request.delete(`/api/admin/events/${id}`, { headers: { 'X-CSRF-Token': csrf } })
  }
})

test('committee sets both closing times and a hold in days', async ({ page, request }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')

  await page.goto('/admin')
  await page.getByRole('button', { name: 'Events', exact: true }).click()
  const card = page.locator('div.card, [class*="card"]').filter({ hasText: OPEN.title })
  await card.getByRole('button', { name: 'Edit', exact: true }).click()

  await page.locator('.ees-field').filter({ hasText: 'Hold (days)' }).locator('input').fill('3')
  await page.getByLabel('Stop taking reservations at a set time').check()
  await page.getByLabel('Reservations close').fill(RESERVE_CLOSE_INPUT)
  await page.getByLabel('The Union shop stops selling at a set time').check()
  await page.getByLabel('Union sales close').fill(UNION_CLOSE_INPUT)
  await page.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect(page.getByText('Event updated.')).toBeVisible()

  // What was saved, read back through the api.
  const events = await (await request.get('/api/admin/events')).json()
  const saved = events.find((e) => e.id === ids.open)
  expect(saved.hold_hours).toBe(72)
  // datetime-local is the browser's own clock; compare as instants.
  expect(new Date(saved.reservations_close_at).getTime()).toBe(new Date(RESERVE_CLOSE_INPUT).getTime())
  expect(new Date(saved.union_sales_close_at).getTime()).toBe(new Date(UNION_CLOSE_INPUT).getTime())

  // And the public page now says when booking closes.
  await page.goto(`/events/${OPEN.slug}`)
  await expect(page.locator('.booking-closes')).toContainText('Booking closes')
})

test('once booking has closed the page says so and offers no way to book', async ({ page }) => {
  await page.goto(`/events/${SHUT.slug}`)
  await expect(page.locator('.booking-closed')).toContainText('Booking has closed')
  await expect(page.getByRole('button', { name: /^(Reserve|Book my place)$/ })).toHaveCount(0)
})
