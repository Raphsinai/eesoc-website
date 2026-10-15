// Creating an event from the committee admin's New event form, for the two
// ways it went wrong on 2026-10-15: a ticket priced 17.5 in a field that took
// whole pence, refused by the API, and that refusal shown as "[object Object]"
// because a validation error's detail is a list, not a sentence.
//
// NEEDS THE COMPOSE TEST STACK — runs in CI only (see tests/README.md).
const { test, expect } = require('@playwright/test')

const STAMP = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const created = []

// The form's controls are Field-wrapped and the labels are not wired with
// `for`, so find the field by its exact label text.
function field(page, label) {
  return page
    .locator('.ees-field')
    .filter({ has: page.locator('label.ees-label', { hasText: new RegExp(`^${label}$`) }) })
    .locator('input')
    .first()
}

async function openNewEventForm(page, slug) {
  await page.goto('/admin')
  await page.getByRole('button', { name: 'Events', exact: true }).click()
  await field(page, 'Title').fill(`E2E event form ${slug}`)
  await field(page, 'Slug').fill(slug)
  await field(page, 'Starts').fill('2030-05-10T19:00')
  await field(page, 'Ends').fill('2030-05-10T22:00')
}

test.afterAll(async ({ request }) => {
  if (!created.length) return
  const login = await request.post('/api/auth/dev-login', {
    data: { email: `event-form-teardown-${STAMP}@example.com`, make_admin: true },
  })
  const csrf = (await login.json()).csrf_token
  for (const id of created) {
    await request.delete(`/api/admin/events/${id}`, { headers: { 'X-CSRF-Token': csrf } })
  }
})

test('a ticket price typed in pounds is saved in pence', async ({ page }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')
  const slug = `e2e-form-price-${STAMP}`

  await openNewEventForm(page, slug)
  await page.getByRole('button', { name: 'Add ticket type' }).click()
  await field(page, 'Name').fill('Early Bird')
  await field(page, 'Price \\(£\\)').fill('17.50')
  await page.getByRole('button', { name: 'Create event', exact: true }).click()
  await expect(page.getByText('Event created.')).toBeVisible()

  const events = await (await page.request.get('/api/admin/events')).json()
  const event = events.find((e) => e.slug === slug)
  expect(event, 'the event was created').toBeTruthy()
  created.push(event.id)
  expect(event.ticket_types.map((t) => t.price_pence)).toEqual([1750])
})

test('a refused save names the field, not [object Object]', async ({ page }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')
  const slug = `e2e-form-error-${STAMP}`

  await openNewEventForm(page, slug)
  // Not a whole number: the API's validation refuses it with a list of
  // {loc, msg}, the shape that used to print as [object Object].
  await field(page, 'Total capacity').fill('1.5')
  await page.getByRole('button', { name: 'Create event', exact: true }).click()

  const message = page.getByText(/capacity: /)
  await expect(message).toBeVisible()
  await expect(page.getByText('[object Object]')).toHaveCount(0)

  // Nothing was created by the refused save.
  const events = await (await page.request.get('/api/admin/events')).json()
  const stray = events.find((e) => e.slug === slug)
  if (stray) created.push(stray.id)
  expect(stray).toBeUndefined()
})
