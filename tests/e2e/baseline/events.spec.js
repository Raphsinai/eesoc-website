// The events calendar lists entries and one event's page loads.
//
// Our events table is empty on a fresh stack -- the union mirror carries
// members/committee/products/sales, not events -- so this spec creates one
// published, upcoming event through the admin API first, then reads it back
// exactly the way the SPA does.
const { test, expect } = require('@playwright/test')

// Unique per run so repeated CI runs never contend for a slug, and never hit a
// dev-login account that already exists in some odd state.
const SLUG = `e2e-event-${Date.now()}`
const TITLE = `E2E baseline event ${SLUG.slice(-6)}`
const SETUP_EMAIL = `events-setup-${Date.now()}@example.com`
let EVENT_ID = null

async function signInAsAdmin(request) {
  // dev-login is live on the public instance (DEV_LOGIN_ENABLED=true) and needs
  // no CSRF header; it sets the session + csrf cookies on this request context.
  const login = await request.post('/api/auth/dev-login', {
    data: { email: SETUP_EMAIL, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

test.beforeAll(async ({ request }) => {
  const csrfToken = await signInAsAdmin(request)

  const create = await request.post('/api/admin/events', {
    data: {
      slug: SLUG,
      title: TITLE,
      category: 'Workshops',
      venue: 'EEE 403',
      // Thirty days out keeps it in the "upcoming" view the calendar opens on.
      starts_at: new Date(Date.now() + 30 * 86400000).toISOString(),
      published: true,
    },
    headers: { 'X-CSRF-Token': csrfToken },
  })
  expect(create.ok()).toBeTruthy()
  EVENT_ID = (await create.json()).id
})

test.afterAll(async ({ request }) => {
  if (EVENT_ID == null) return
  const csrfToken = await signInAsAdmin(request)
  await request.delete(`/api/admin/events/${EVENT_ID}`, {
    headers: { 'X-CSRF-Token': csrfToken },
  })
})

test('calendar page lists published events', async ({ page }) => {
  await page.goto('/events')

  // The heading counts upcoming events ("N events coming up.").
  await expect(page.getByRole('heading', { level: 1, name: /coming up/i })).toBeVisible()

  // EventCard renders the title as an <h3>.
  await expect(page.getByRole('heading', { level: 3, name: TITLE })).toBeVisible()
})

test('one event page loads', async ({ page }) => {
  await page.goto(`/events/${SLUG}`)

  // The title is the page's <h1>, and the back-link proves the view mounted.
  await expect(page.getByRole('heading', { level: 1, name: TITLE })).toBeVisible()
  await expect(page.getByRole('link', { name: /All events/ })).toBeVisible()
})