// The email approval queue, as a committee member uses it: an on-sale email
// the site wrote on its own sits in "Waiting for approval" on the Email tab,
// opens for review with a preview of the whole message, takes an edit, and
// can be dismissed without anything being sent.
//
// NEEDS THE COMPOSE TEST STACK — runs in CI only (see tests/README.md). The
// event is created through the admin write path in beforeAll, and the on-sale
// pass is run through the api, so the spec never depends on the scheduler's
// clock. Sending is covered against mailpit by tests/email-approval/.
const { test, expect } = require('@playwright/test')

const STAMP = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const SLUG = `e2e-approval-${STAMP}`
const TITLE = `E2E approval ${STAMP}`
const SETUP_EMAIL = `approval-setup-${STAMP}@example.com`

let EVENT_ID = null

async function signInAsAdmin(request) {
  const login = await request.post('/api/auth/dev-login', {
    data: { email: SETUP_EMAIL, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

async function draftsForEvent(request) {
  const res = await request.get('/api/admin/email/queue')
  expect(res.ok()).toBeTruthy()
  return (await res.json()).items.filter((q) => q.event && q.event.id === EVENT_ID)
}

test.beforeAll(async ({ request }) => {
  const csrfToken = await signInAsAdmin(request)
  const hour = 3600 * 1000
  const created = await request.post('/api/admin/events', {
    data: {
      slug: SLUG,
      title: TITLE,
      category: 'Socials',
      venue: 'EEE 403',
      starts_at: new Date(Date.now() + 30 * 24 * hour).toISOString(),
      ends_at: new Date(Date.now() + 30 * 24 * hour + 2 * hour).toISOString(),
      // Already open, so the on-sale pass writes its email straight away.
      sales_open_at: new Date(Date.now() - hour).toISOString(),
      published: true,
    },
    headers: { 'X-CSRF-Token': csrfToken },
  })
  expect(created.ok()).toBeTruthy()
  EVENT_ID = (await created.json()).id

  // Polled: a pass already running (the scheduler's tick) makes announce-now
  // skip, and that pass writes the draft a moment later.
  await expect(async () => {
    await request.post('/api/admin/email/announce-now', {
      headers: { 'X-CSRF-Token': csrfToken },
    })
    expect(await draftsForEvent(request)).toHaveLength(1)
  }).toPass({ timeout: 20_000 })
})

test.afterAll(async ({ request }) => {
  const csrfToken = await signInAsAdmin(request)
  if (EVENT_ID == null) return
  for (const draft of await draftsForEvent(request)) {
    await request.post(`/api/admin/email/queue/${draft.id}/dismiss`, {
      headers: { 'X-CSRF-Token': csrfToken },
    })
  }
  await request.delete(`/api/admin/events/${EVENT_ID}`, {
    headers: { 'X-CSRF-Token': csrfToken },
  })
})

test('committee reviews, edits and dismisses an on-sale email waiting for approval', async ({ page }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')
  // Dismiss asks "are you sure" with window.confirm.
  page.on('dialog', (d) => d.accept())

  await page.goto('/admin?tab=email')
  const row = page.locator('.queue-row').filter({ hasText: TITLE })
  await expect(row).toBeVisible()
  await expect(row).toContainText('Tickets on sale')
  await row.getByRole('button', { name: 'Review' }).click()

  const review = page.getByRole('dialog', { name: /^Review/ })
  await expect(review).toBeVisible()
  // The whole message, event details included — not just the editable part.
  await expect(review.locator('.mail-preview')).toContainText(TITLE)
  await expect(review.locator('.mail-preview')).toContainText('EEE 403')

  // Subject is the first field in the dialog. Edit, save, and the preview
  // follows — it is rendered by the server from the saved draft.
  const edited = `Edited ${STAMP}`
  await review.locator('.ees-field input').first().fill(edited)
  await review.getByRole('button', { name: 'Save and update the preview' }).click()
  await expect(review).toContainText(`“${edited}”`)

  await review.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await expect(review).toHaveCount(0)
  await expect(page.locator('.queue-row').filter({ hasText: STAMP })).toHaveCount(0)
})
