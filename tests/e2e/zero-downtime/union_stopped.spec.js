// The site's own pages keep rendering when the union bridge is stopped.
//
// Events are served from the api's database, and the home page's hero is its
// own static copy, so a bridge outage must not take either down. This spec
// stops the union stub for the duration of the browser checks, then starts it
// again so any suite after it still has the bridge its data depends on.
//
// The E2E rollout job sets EESOC_ROLLOUT_E2E; the shared Playwright run does
// not, and there the whole describe is skipped.
const { test, expect } = require('@playwright/test')
const { execSync } = require('child_process')
const path = require('path')

const REPO_ROOT = path.resolve(__dirname, '../../..')

// Same project name and compose files the pytest suites use, so the stop/start
// hits the exact stack the test frontend is proxying into.
const COMPOSE = [
  'docker', 'compose',
  '-f', 'docker-compose.yml',
  '-f', 'docker-compose.test.yml',
  '-p', 'eesoc-test',
]

// Unique per run, following the baseline events spec, so repeated CI runs never
// contend for a slug and the event can be cleaned up knowing it is ours.
const SLUG = `e2e-union-stopped-${Date.now()}`
const TITLE = `E2E union-stopped event ${SLUG.slice(-6)}`
const SETUP_EMAIL = `union-stopped-setup-${Date.now()}@example.com`
let EVENT_ID = null

function compose(...args) {
  execSync([...COMPOSE, ...args].join(' '), { cwd: REPO_ROOT })
}

async function signInAsAdmin(request) {
  const login = await request.post('/api/auth/dev-login', {
    data: { email: SETUP_EMAIL, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

test.describe('site renders with the union bridge stopped', () => {
  test.skip(!process.env.EESOC_ROLLOUT_E2E, 'rollout E2E gate not set')

  test.beforeAll(async ({ request }) => {
    // Create a published, upcoming event up front: the events page assertion
    // needs one card to look for, and the plan's own point is that the page
    // serves it from our database, not the (about to be stopped) bridge.
    const csrfToken = await signInAsAdmin(request)
    const create = await request.post('/api/admin/events', {
      data: {
        slug: SLUG,
        title: TITLE,
        category: 'Workshops',
        venue: 'EEE 403',
        starts_at: new Date(Date.now() + 30 * 86400000).toISOString(),
        published: true,
      },
      headers: { 'X-CSRF-Token': csrfToken },
    })
    expect(create.ok()).toBeTruthy()
    EVENT_ID = (await create.json()).id

    compose('stop', 'union-stub')
  })

  test.afterAll(async ({ request }) => {
    compose('up', '-d', '--no-deps', 'union-stub')
    if (EVENT_ID == null) return
    const csrfToken = await signInAsAdmin(request)
    await request.delete(`/api/admin/events/${EVENT_ID}`, {
      headers: { 'X-CSRF-Token': csrfToken },
    })
  })

  test('home page renders its hero with the bridge down', async ({ page }) => {
    await page.goto('/')

    // The hero headline from HomeView.vue, which is static page copy and does
    // not come from the union mirror.
    await expect(page.getByRole('heading', { name: /Build things/ })).toBeVisible()
  })

  test('events page lists an event card from our database', async ({ page }) => {
    await page.goto('/events')

    // The "N events coming up" heading is the page loading its own data.
    await expect(
      page.getByRole('heading', { level: 1, name: /coming up/i })
    ).toBeVisible()

    // EventCard renders the title as an <h3> — the event was created through
    // our admin API, so seeing it here proves local serving, not the bridge.
    await expect(page.getByRole('heading', { level: 3, name: TITLE })).toBeVisible()
  })
})