// Ticket types on sale at different times (migration 080): the committee gives
// general admission a later sale time than early bird in the event editor, and
// the event page says when general goes on sale while early bird can be booked.
//
// NEEDS THE COMPOSE TEST STACK — runs in CI only (see tests/README.md). The
// event is created through the admin write path in beforeAll.
const { test, expect } = require('@playwright/test')

const STAMP = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const SETUP_EMAIL = `tt-windows-setup-${STAMP}@example.com`
const EVENT = { slug: `e2e-tt-window-${STAMP}`, title: `E2E ticket windows ${STAMP}` }
// A May evening, away from any clock change, as the sale-windows spec does.
const STARTS_AT = '2030-05-10T19:00:00.000Z'
const GENERAL_OPENS_INPUT = '2030-05-01T10:00'

const ids = {}

async function signInAsAdmin(request) {
  const login = await request.post('/api/auth/dev-login', {
    data: { email: SETUP_EMAIL, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

test.beforeAll(async ({ request }) => {
  const csrf = await signInAsAdmin(request)
  const made = await request.post('/api/admin/events', {
    data: {
      slug: EVENT.slug,
      title: EVENT.title,
      category: 'Socials',
      venue: 'EEE 403',
      starts_at: STARTS_AT,
      ends_at: '2030-05-10T22:00:00.000Z',
      sales_open_at: new Date(Date.now() - 24 * 3600 * 1000).toISOString(),
      published: true,
      ticket_types: [
        { name: 'Early bird', price_pence: 0, capacity: 20, requires_purchase: false },
        { name: 'General', price_pence: 0, capacity: 20, requires_purchase: false, sort_order: 200 },
      ],
    },
    headers: { 'X-CSRF-Token': csrf },
  })
  expect(made.ok()).toBeTruthy()
  ids.event = (await made.json()).id
})

test.afterAll(async ({ request }) => {
  const csrf = await signInAsAdmin(request)
  for (const id of Object.values(ids)) {
    await request.delete(`/api/admin/events/${id}`, { headers: { 'X-CSRF-Token': csrf } })
  }
})

test('committee gives general a later sale time than early bird', async ({ page, request }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')

  await page.goto('/admin')
  await page.getByRole('button', { name: 'Events', exact: true }).click()
  const card = page.locator('div.card, [class*="card"]').filter({ hasText: EVENT.title })
  await card.getByRole('button', { name: 'Edit', exact: true }).click()

  await page.getByTestId('ticket-windows').check()
  const general = page.getByTestId('ticket-window-1')
  await expect(general).toContainText('General')
  await general.getByLabel("Goes on sale later than the event's sale date").check()
  await page.getByLabel('General goes on sale').fill(GENERAL_OPENS_INPUT)
  await page.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect(page.getByText('Event updated.')).toBeVisible()

  // What was saved, read back through the api.
  const events = await (await request.get('/api/admin/events')).json()
  const saved = events.find((e) => e.id === ids.event)
  const byName = Object.fromEntries(saved.ticket_types.map((t) => [t.name, t]))
  // datetime-local is the browser's own clock; compare as instants.
  expect(new Date(byName.General.sales_open_at).getTime()).toBe(new Date(GENERAL_OPENS_INPUT).getTime())
  expect(byName['Early bird'].sales_open_at).toBeNull()

  // The public page: early bird can be booked, general says when it opens.
  await page.goto(`/events/${EVENT.slug}`)
  const options = page.locator('select.ees-select option')
  await expect(options.filter({ hasText: 'General' })).toContainText('on sale')
  await expect(options.filter({ hasText: 'General' })).toBeDisabled()
  await expect(options.filter({ hasText: 'Early bird' })).toBeEnabled()
})
