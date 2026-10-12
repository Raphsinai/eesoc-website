// The email queue, as a committee member uses it: publishing an event with a
// sale time puts its "tickets are on sale" email in the queue straight away;
// it opens for review with a preview of the whole message, takes an edit, is
// approved for when sales open, taken back off the schedule, and dismissed —
// without anything being sent.
//
// NEEDS THE COMPOSE TEST STACK — runs in CI only (see tests/README.md). The
// event is created through the admin write path in beforeAll. Sending, and
// sending at the scheduled time, are covered against mailpit by
// tests/email-approval/.
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
      // A day ahead: the email is drafted now, to be approved for then.
      sales_open_at: new Date(Date.now() + 24 * hour).toISOString(),
      published: true,
    },
    headers: { 'X-CSRF-Token': csrfToken },
  })
  expect(created.ok()).toBeTruthy()
  EVENT_ID = (await created.json()).id
  // Written by the save itself — no timer to wait for.
  expect(await draftsForEvent(request)).toHaveLength(1)
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

test('committee reviews, schedules, unschedules and dismisses an on-sale email', async ({ page }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')
  // Approve and Dismiss both ask "are you sure" with window.confirm.
  page.on('dialog', (d) => d.accept())

  await page.goto('/admin?tab=email')
  const row = page.locator('.queue-row').filter({ hasText: TITLE })
  await expect(row).toBeVisible()
  await expect(row).toContainText('needs approval')
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

  // Approve it for when sales open.
  await review.getByRole('button', { name: /^Approve for / }).click()
  await expect(review).toHaveCount(0)
  const scheduled = page.locator('.queue-row').filter({ hasText: STAMP })
  await expect(scheduled).toContainText('approved')
  await expect(scheduled).toContainText('goes out')

  // While approved it is read-only; take it back off the schedule.
  await scheduled.getByRole('button', { name: 'Review' }).click()
  await expect(review.locator('.review-scheduled')).toBeVisible()
  await expect(review.locator('.ees-field input').first()).toBeDisabled()
  await review.getByRole('button', { name: 'Take off schedule' }).click()
  await expect(review.locator('.review-scheduled')).toHaveCount(0)
  await expect(review.locator('.ees-field input').first()).toBeEnabled()

  await review.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await expect(review).toHaveCount(0)
  await expect(page.locator('.queue-row').filter({ hasText: STAMP })).toHaveCount(0)
})
