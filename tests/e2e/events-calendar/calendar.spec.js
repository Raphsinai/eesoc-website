// The events calendar links, the shareable .ics feed, and how the private
// meeting link is gated to booked attendees.
//
// This spec reads public pages and public endpoints, so it opts OUT of the
// `admin` project's signed-in storage state for every test — an admin would
// legitimately see a private meeting link, which would make the "a stranger
// does not see it" assertion lie. Each test that needs a session mints its own.
//
// NEEDS THE COMPOSE TEST STACK — runs in CI only (see tests/README.md); it
// talks to the api through the browser's /api origin and creates its events
// through the admin write path exactly the way the SPA does.
const { test, expect } = require('@playwright/test')

// Run every test signed out, even under the `admin` project, so "anonymous"
// means anonymous for all of the gating assertions below.
test.use({ storageState: { cookies: [], origins: [] } })

// Unique per run so repeated CI runs never contend for a slug. The random
// suffix is load-bearing: this spec runs twice (public + admin projects) and
// the two runs can start inside the same millisecond, so `Date.now()` alone
// collides on the slug and fails every test with a 409 before it starts.
const STAMP = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const SLUG_A = `e2e-cal-a-${STAMP}`
const SLUG_B = `e2e-cal-b-${STAMP}`
const TITLE_A = `E2E calendar event A ${STAMP}`
const TITLE_B = `E2E calendar event B ${STAMP}`
const MAP_URL_A = 'https://example.com/venue-map-a'
const SIGNUP_URL_A = 'https://example.com/signup-a'
const MEETING_URL_A = 'https://meet.example.com/e2e-room-a'
const MEETING_URL_B = 'https://meet.example.com/e2e-private-room-b'
const SETUP_EMAIL = `events-cal-setup-${STAMP}@example.com`

// The event page is a public route, so navigation does not depend on which
// project this spec is running under.
const EVENT_A = `/events/${SLUG_A}`
const EVENT_B = `/events/${SLUG_B}`

let EVENT_ID_A = null
let EVENT_ID_B = null
let TICKET_TYPE_ID_B = null

async function signInAsAdmin(request) {
  // dev-login is live on the test stack (DEV_LOGIN_ENABLED=true), needs no CSRF
  // header, and sets the session + csrf cookies on this request context.
  const login = await request.post('/api/auth/dev-login', {
    data: { email: SETUP_EMAIL, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

test.beforeAll(async ({ request }) => {
  const csrfToken = await signInAsAdmin(request)
  const future = new Date(Date.now() + 30 * 86400000).toISOString()
  // The Google Calendar link only renders when the event has both a start and
  // an end (the render template needs a duration), so Event A gets an end two
  // hours after its start. Without it the `event-gcal-link` assertion below
  // would fail: the UI deliberately leaves the link off rather than invent one.
  const futureEnd = new Date(Date.now() + 30 * 86400000 + 2 * 3600000).toISOString()
  const past = new Date(Date.now() - 86400000).toISOString()

  // Event A: public meeting link, venue map, external sign-up, aimed at years
  // 1 and 2 — exercises every new link field at once.
  const createA = await request.post('/api/admin/events', {
    data: {
      slug: SLUG_A,
      title: TITLE_A,
      category: 'Workshops',
      venue: 'EEE 403',
      starts_at: future,
      ends_at: futureEnd,
      published: true,
      map_url: MAP_URL_A,
      signup_url: SIGNUP_URL_A,
      meeting_url: MEETING_URL_A,
      meeting_url_private: false,
      target_years: [1, 2],
    },
    headers: { 'X-CSRF-Token': csrfToken },
  })
  expect(createA.ok()).toBeTruthy()
  EVENT_ID_A = (await createA.json()).id

  // Event B: a PRIVATE meeting link plus a free non-purchase ticket type whose
  // sales are already open, so a visitor can actually book it.
  const createB = await request.post('/api/admin/events', {
    data: {
      slug: SLUG_B,
      title: TITLE_B,
      category: 'Workshops',
      venue: 'EEE 510',
      starts_at: future,
      published: true,
      meeting_url: MEETING_URL_B,
      meeting_url_private: true,
      sales_open_at: past,
      ticket_types: [
        { name: 'Free seat', price_pence: 0, capacity: 5, requires_purchase: false },
      ],
    },
    headers: { 'X-CSRF-Token': csrfToken },
  })
  expect(createB.ok()).toBeTruthy()
  EVENT_ID_B = (await createB.json()).id
})

test.afterAll(async ({ request }) => {
  const csrfToken = await signInAsAdmin(request)
  // Remove both events so later runs never accumulate calendar entries.
  for (const id of [EVENT_ID_A, EVENT_ID_B]) {
    if (id != null) {
      await request.delete(`/api/admin/events/${id}`, {
        headers: { 'X-CSRF-Token': csrfToken },
      })
    }
  }
})

test('event page shows calendar, map, meeting and sign-up links', async ({ page }) => {
  await page.goto(EVENT_A)

  // The .ics download points at the per-event calendar endpoint.
  await expect(page.getByTestId('event-ics-link')).toBeVisible()
  await expect(page.getByTestId('event-ics-link')).toHaveAttribute(
    'href',
    `/api/events/${SLUG_A}/calendar.ics`,
  )

  // The Google Calendar link carries the render endpoint, whatever parameters
  // the client appends after it.
  await expect(page.getByTestId('event-gcal-link')).toBeVisible()
  await expect(page.getByTestId('event-gcal-link')).toHaveAttribute(
    'href',
    /^https:\/\/calendar\.google\.com\/calendar\/render/,
  )

  // The venue map link is the stored URL, and the meeting link (non-private)
  // and the external sign-up link also render for an anonymous visitor.
  await expect(page.getByTestId('event-map-link')).toHaveAttribute('href', MAP_URL_A)
  await expect(page.getByTestId('event-meeting-link')).toHaveAttribute('href', MEETING_URL_A)
  await expect(page.getByTestId('event-signup-link')).toBeVisible()
})

test('event without a sign-up URL shows no sign-up link', async ({ page }) => {
  await page.goto(EVENT_B)

  // Event B has no external sign-up, so the card must not render that link at all.
  await expect(page.getByTestId('event-signup-link')).toHaveCount(0)
})

test('a non-booked visitor does not see the private meeting link', async ({ page }) => {
  // The file-wide storageState opt-out guarantees this runs signed out even
  // under the `admin` project, where the admin would be entitled to see it.
  await page.goto(EVENT_B)
  await expect(page.getByTestId('event-meeting-link')).toHaveCount(0)
})

test('a booked member sees the private meeting link', async ({ page }) => {
  // Sign in as member1 through the page's own request context so the session
  // cookies apply to the subsequent page load.
  const login = await page.request.post('/api/auth/dev-login', {
    data: { email: 'member1@ic.ac.uk' },
  })
  expect(login.ok()).toBeTruthy()
  const csrfToken = (await login.json()).csrf_token

  // Read the free ticket type id from the public payload, then hold a seat as
  // the member — member1 is on the stub roster, and the free ticket needs no
  // purchase to become a live (held) booking.
  if (TICKET_TYPE_ID_B == null) {
    const detail = await page.request.get(`/api/events/${SLUG_B}`)
    TICKET_TYPE_ID_B = (await detail.json()).ticketTypes[0].id
  }
  const reserved = await page.request.post(`/api/events/${SLUG_B}/reserve`, {
    data: { ticket_type_id: TICKET_TYPE_ID_B, quantity: 1 },
    headers: { 'X-CSRF-Token': csrfToken },
  })
  expect(reserved.ok()).toBeTruthy()

  // The API now surfaces the meeting link for this holder; the page renders it.
  await page.goto(EVENT_B)
  await expect(page.getByTestId('event-meeting-link')).toBeVisible()
  await expect(page.getByTestId('event-meeting-link')).toHaveAttribute('href', MEETING_URL_B)
})

test('the events page shows the calendar subscribe link', async ({ page }) => {
  await page.goto('/events')

  // The subscribe link feeds the all-events .ics into whatever calendar app the
  // visitor uses.
  const link = page.getByTestId('calendar-subscribe-link')
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', '/api/events.ics')
})

test('the feed is public and leaks no private meeting link', async ({ request }) => {
  // No session is sent here (the storage state is empty), which is exactly what
  // a calendar client does.
  const response = await request.get('/api/events.ics')
  expect(response.status()).toBe(200)
  expect(response.headers()['content-type']).toContain('text/calendar')

  const body = await response.text()
  expect(body).toContain(TITLE_A)
  // The one thing that must never survive into the shareable feed: the URL of
  // a private meeting room.
  expect(body).not.toContain(MEETING_URL_B)
})