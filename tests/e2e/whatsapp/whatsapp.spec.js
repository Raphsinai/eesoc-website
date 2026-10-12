// Announcing an event on WhatsApp, as a committee member does it: open the
// event's Announce dialog, write a line, ask for the WhatsApp version, and get
// a post with the event's details in it and a wa.me link that carries exactly
// that post. Editing the words updates both. Nothing is sent by the site —
// the link only opens WhatsApp.
//
// NEEDS THE COMPOSE TEST STACK — runs in CI only (see tests/README.md). The
// formatting rules themselves are pinned by tests/whatsapp/.
const { test, expect } = require('@playwright/test')

const STAMP = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const SLUG = `e2e-whatsapp-${STAMP}`
const TITLE = `E2E WhatsApp ${STAMP}`
const SETUP_EMAIL = `whatsapp-setup-${STAMP}@example.com`

let EVENT_ID = null

async function signInAsAdmin(request) {
  const login = await request.post('/api/auth/dev-login', {
    data: { email: SETUP_EMAIL, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

/** The post the "Open in WhatsApp" link would hand to the app. */
async function linkedText(link) {
  const href = await link.getAttribute('href')
  const url = new URL(href)
  expect(url.origin + url.pathname).toBe('https://wa.me/')
  return url.searchParams.get('text')
}

test.beforeAll(async ({ request }) => {
  const csrfToken = await signInAsAdmin(request)
  const hour = 3600 * 1000
  // No sale time, so no on-sale email is drafted into the queue by this.
  const created = await request.post('/api/admin/events', {
    data: {
      slug: SLUG,
      title: TITLE,
      category: 'Socials',
      venue: 'EEE 403',
      starts_at: new Date(Date.now() + 30 * 24 * hour).toISOString(),
      ends_at: new Date(Date.now() + 30 * 24 * hour + 2 * hour).toISOString(),
      published: true,
    },
    headers: { 'X-CSRF-Token': csrfToken },
  })
  expect(created.ok()).toBeTruthy()
  EVENT_ID = (await created.json()).id
})

test.afterAll(async ({ request }) => {
  if (EVENT_ID == null) return
  const csrfToken = await signInAsAdmin(request)
  await request.delete(`/api/admin/events/${EVENT_ID}`, {
    headers: { 'X-CSRF-Token': csrfToken },
  })
})

test('committee writes an event announcement for WhatsApp', async ({ page }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')

  await page.goto('/admin?tab=events')
  // The innermost card naming the event: its own row, not a list around it.
  const row = page.locator('.ees-card').filter({ hasText: TITLE }).last()
  await row.getByRole('button', { name: 'Announce', exact: true }).click()

  const dialog = page.getByRole('dialog', { name: `Announce ${TITLE}` })
  await expect(dialog).toBeVisible()
  await dialog.locator('textarea').fill('Bring **a friend**.')
  await dialog.getByRole('button', { name: 'WhatsApp version' }).click()

  const post = dialog.locator('.wa__text')
  const open = dialog.getByRole('link', { name: 'Open in WhatsApp' })
  // WhatsApp's bold, the event's details from the database, and its link.
  await expect(post).toContainText('Bring *a friend*.')
  await expect(post).toContainText('*Where:* EEE 403')
  await expect(post).toContainText(`/events/${SLUG}`)
  expect(await linkedText(open)).toBe(await post.textContent())

  // The post follows the words; the link is never older than the text.
  await dialog.locator('textarea').fill('Bring **two** friends.')
  await expect(post).toContainText('Bring *two* friends.')
  await expect(open).toBeVisible()
  expect(await linkedText(open)).toContain('Bring *two* friends.')
})

test('a newsletter has a WhatsApp version too', async ({ page }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')

  await page.goto('/admin?tab=email')
  await page.getByPlaceholder('Careers Fair, lockers and the hack').fill(`AGM ${STAMP}`)
  await page.locator('textarea.ees-textarea').first().fill('## Agenda\n\n- Elections\n- Pizza')
  await page.getByRole('button', { name: 'WhatsApp version' }).click()

  // The subject is the bold first line; the heading and bullets follow.
  const expected = `*AGM ${STAMP}*\n\n*Agenda*\n\n- Elections\n- Pizza`
  const post = page.locator('.wa__text')
  await expect(post).toContainText('- Pizza')
  expect(await post.textContent()).toBe(expected)
  expect(await linkedText(page.getByRole('link', { name: 'Open in WhatsApp' }))).toBe(expected)
})
