// The doors-close time: set it in the committee admin (spec 14, criterion 7),
// see it above the fold on the public event page (criterion 8).
//
// NEEDS THE COMPOSE TEST STACK — runs in CI only (see tests/README.md). The
// admin-edit test drives the real AdminView form exactly the way a committee
// member does; the public-page test reads the event page an anonymous visitor
// gets. Both events are created through the admin write path in beforeAll so
// the spec never depends on a fixture database.
const { test, expect } = require('@playwright/test')

// Unique per run so repeated CI runs never contend for a slug (the pattern
// events-calendar uses; see its comment on why Date.now() alone collides).
const STAMP = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const SLUG_WITH = `e2e-doors-a-${STAMP}`
const SLUG_EDIT = `e2e-doors-b-${STAMP}`
const TITLE_WITH = `E2E doors close A ${STAMP}`
const TITLE_EDIT = `E2E doors close B ${STAMP}`
const SETUP_EMAIL = `doors-close-setup-${STAMP}@example.com`

// A May evening: safely away from any DST transition, so the local↔UTC round
// trip in the admin form is the same in every timezone a CI runner might use.
// 17:15Z is 18:15 in London (BST), and the same London day as the 19:00Z
// start — so the public payload's doorsClose is the bare time "18:15".
const STARTS_AT = '2030-05-10T19:00:00.000Z'
const DOORS_CLOSE_AT = '2030-05-10T17:15:00.000Z'
// What the admin types into the datetime-local field, and what it must read
// back after a save + reload (the form converts through the browser's clock,
// so the same value comes back in any timezone).
const EDIT_INPUT = '2030-05-10T18:15'

let EVENT_ID_WITH = null
let EVENT_ID_EDIT = null

async function signInAsAdmin(request) {
  // dev-login is live on the test stack (DEV_LOGIN_ENABLED=true), needs no
  // CSRF header, and sets the session + csrf cookies on this request context.
  const login = await request.post('/api/auth/dev-login', {
    data: { email: SETUP_EMAIL, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

test.beforeAll(async ({ request }) => {
  const csrfToken = await signInAsAdmin(request)

  // Event A carries a doors-close time from the start, for the public page.
  const createWith = await request.post('/api/admin/events', {
    data: {
      slug: SLUG_WITH,
      title: TITLE_WITH,
      category: 'Workshops',
      venue: 'EEE 403',
      starts_at: STARTS_AT,
      ends_at: '2030-05-10T21:00:00.000Z',
      doors_close_at: DOORS_CLOSE_AT,
      published: true,
    },
    headers: { 'X-CSRF-Token': csrfToken },
  })
  expect(createWith.ok()).toBeTruthy()
  EVENT_ID_WITH = (await createWith.json()).id

  // Event B has none yet; the admin test adds it through the form.
  const createEdit = await request.post('/api/admin/events', {
    data: {
      slug: SLUG_EDIT,
      title: TITLE_EDIT,
      category: 'Workshops',
      venue: 'EEE 403',
      starts_at: STARTS_AT,
      ends_at: '2030-05-10T21:00:00.000Z',
      published: true,
    },
    headers: { 'X-CSRF-Token': csrfToken },
  })
  expect(createEdit.ok()).toBeTruthy()
  EVENT_ID_EDIT = (await createEdit.json()).id
})

test.afterAll(async ({ request }) => {
  const csrfToken = await signInAsAdmin(request)
  // Remove both events so later runs never accumulate them.
  for (const id of [EVENT_ID_WITH, EVENT_ID_EDIT]) {
    if (id != null) {
      await request.delete(`/api/admin/events/${id}`, {
        headers: { 'X-CSRF-Token': csrfToken },
      })
    }
  }
})

test('public event page shows the doors-close callout above the fold at 390x844', async ({ page }) => {
  // The phone from the spec: the callout must be in the first viewport, not
  // just somewhere on the page, or the one hard time limit of the evening
  // might as well not be printed.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/events/${SLUG_WITH}`)

  const callout = page.locator('.event-doors-close')
  await expect(callout).toBeVisible()
  const text = (await callout.innerText()).replace(/\s+/g, ' ')
  expect(text).toContain('Doors close 18:15')
  expect(text).toContain('no entry after')

  // Above the fold: the whole callout sits inside the first 844px viewport.
  const box = await callout.boundingBox()
  expect(box).not.toBeNull()
  expect(box.y + box.height).toBeLessThanOrEqual(844)
})

test('public event page shows no doors-close callout when the event has none', async ({ page }) => {
  // Event B has no doors-close time yet (the admin test adds one), so the
  // callout must not render at all — an invented time is worse than none.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/events/${SLUG_EDIT}`)

  await expect(page.locator('.event-doors-close')).toHaveCount(0)
})

test('committee admin sets Doors close, saves, reloads, and the value persists', async ({ page }) => {
  // Only the `admin` project carries a committee session; the `public` pass
  // has nothing to load /admin with (see baseline/admin.spec.js).
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')

  await page.goto('/admin')
  await page.getByRole('button', { name: 'Events', exact: true }).click()

  // Open Event B's editor. The card is found by its unique title so the spec
  // never depends on list ordering.
  const card = page.locator('div.card, [class*="card"]').filter({ hasText: TITLE_EDIT })
  await card.getByRole('button', { name: 'Edit', exact: true }).click()

  // The Doors close field, next to Starts/Ends — a datetime-local input whose
  // Field label is not wired to it with `for`, so locate the field by label.
  const doorsField = page.locator('.ees-field').filter({ hasText: 'Doors close' })
  await doorsField.locator('input').fill(EDIT_INPUT)
  // Wait for the actual save response before navigating. Navigating
  // immediately aborts an in-flight PUT and gives a false persistence failure.
  const saved = page.waitForResponse(response =>
    response.url().endsWith(`/api/admin/events/${EVENT_ID_EDIT}`) &&
    response.request().method() === 'PUT'
  )
  await page.getByRole('button', { name: 'Save changes', exact: true }).click()
  const saveResponse = await saved
  expect(saveResponse.ok(), await saveResponse.text()).toBeTruthy()
  expect((await saveResponse.json()).doors_close_at).toBeTruthy()

  // Saved: reload the whole SPA and edit again — persistence must survive a
  // fresh page load, not just the in-memory form state.
  await page.goto('/admin')
  await page.getByRole('button', { name: 'Events', exact: true }).click()
  const cardAgain = page.locator('div.card, [class*="card"]').filter({ hasText: TITLE_EDIT })
  await cardAgain.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(
    page.locator('.ees-field').filter({ hasText: 'Doors close' }).locator('input')
  ).toHaveValue(EDIT_INPUT)
})
