// A paid booking should go straight to the correctly configured Union product;
// a free booking must stay on the event page. The Union destination is intercepted
// so this never contacts the real shop.
const { test, expect } = require('@playwright/test')

test.use({ storageState: { cookies: [], origins: [] } })

const tag = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const slug = `booking-redirect-${tag}`
const shopUrl = 'https://www-s.imperialcollegeunion.org/shop/product/3023-/'
let eventId

async function adminCsrf(request) {
  const login = await request.post('/api/auth/dev-login', {
    data: { email: `redirect-creator-${tag}@example.org`, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

test.beforeAll(async ({ request }) => {
  const csrf = await adminCsrf(request)
  const created = await request.post('/api/admin/events', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      title: `Booking redirect ${tag}`,
      slug,
      category: 'Socials',
      starts_at: new Date(Date.now() + 14 * 86400000).toISOString(),
      sales_open_at: new Date(Date.now() - 86400000).toISOString(),
      published: true,
      ticket_types: [
        { name: 'Paid place', price_pence: 1500, capacity: 10,
          requires_purchase: true, purchase_url: shopUrl },
        { name: 'Free place', price_pence: 0, capacity: 10,
          requires_purchase: false },
      ],
    },
  })
  expect(created.ok()).toBeTruthy()
  eventId = (await created.json()).id
})

test.afterAll(async ({ request }) => {
  if (!eventId) return
  const csrf = await adminCsrf(request)
  await request.delete(`/api/admin/events/${eventId}`, {
    headers: { 'X-CSRF-Token': csrf },
  })
})

test('paid reservation redirects in the same tab to its Union shop product', async ({ page }) => {
  const login = await page.request.post('/api/auth/dev-login', {
    data: { email: 'member1@ic.ac.uk' },
  })
  expect(login.ok()).toBeTruthy()
  await page.route(shopUrl, route => route.fulfill({
    status: 200,
    contentType: 'text/html',
    body: '<html><body><h1>Mock Union shop</h1></body></html>',
  }))

  await page.goto(`/events/${slug}`)
  const reserveResponse = page.waitForResponse(response =>
    response.url().endsWith(`/api/events/${slug}/reserve`) &&
    response.request().method() === 'POST')
  const [response] = await Promise.all([
    reserveResponse,
    page.getByRole('button', { name: 'Reserve', exact: true }).click(),
  ])
  expect(response.ok()).toBeTruthy()
  const result = await response.json()
  expect(result.reservation.status).toBe('held')
  expect(result.next.purchaseUrl).toBe(shopUrl)
  await expect(page).toHaveURL(shopUrl)
  await expect(page.getByRole('heading', { name: 'Mock Union shop' })).toBeVisible()
})

test('free reservation does not redirect to a shop', async ({ page }) => {
  await page.goto(`/events/${slug}`)
  const detail = await page.request.get(`/api/events/${slug}`)
  expect(detail.ok()).toBeTruthy()
  const free = (await detail.json()).ticketTypes.find(t => t.name === 'Free place')
  expect(free).toBeTruthy()
  await page.getByLabel('Ticket type').selectOption(String(free.id))
  await page.getByRole('button', { name: 'Book my place' }).click()
  await page.getByPlaceholder('you@example.com').fill(`redirect-guest-${tag}@example.org`)
  await page.getByRole('button', { name: 'Confirm my place' }).click()
  await expect(page).toHaveURL(new RegExp(`/events/${slug}$`))
  await expect(page.getByText(/You're in\. Nothing to pay/)).toBeVisible()
})
