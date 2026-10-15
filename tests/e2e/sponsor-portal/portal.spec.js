// The sponsor portal as a sponsor sees it, and the committee's standard
// package editor.
//
// A sponsor's dashboard lists their package by clause name and usage — never
// the agreement's wording, which is the committee's — and has no members
// count. The committee sets the standard package's allowances under Sponsors,
// and a sponsor on the standard sees the new number.
//
// NEEDS THE COMPOSE TEST STACK — runs in CI only (see tests/README.md). The
// API rules behind this are pinned by tests/sponsor-portal/.
const { test, expect } = require('@playwright/test')

const STAMP = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
const SLUG = `e2e-portal-${STAMP}`
const NAME = `E2E Portal ${STAMP}`
const SPONSOR_EMAIL = `portal-${STAMP}@example.com`
const SETUP_EMAIL = `portal-setup-${STAMP}@example.com`

const IDEA_TITLE = `E2E Hackathon idea ${STAMP}`

let SPONSOR_ID = null
let IDEA_ID = null

async function signInAsAdmin(request) {
  const login = await request.post('/api/auth/dev-login', {
    data: { email: SETUP_EMAIL, make_admin: true },
  })
  expect(login.ok()).toBeTruthy()
  return (await login.json()).csrf_token
}

async function standardMarketing(request) {
  const res = await request.get('/api/admin/sponsors')
  expect(res.ok()).toBeTruthy()
  return (await res.json()).package.find((c) => c.key === 'marketing').allowance
}

test.beforeAll(async ({ request }) => {
  const csrfToken = await signInAsAdmin(request)
  const headers = { 'X-CSRF-Token': csrfToken }
  const created = await request.post('/api/admin/sponsors', {
    data: { slug: SLUG, name: NAME, active: false },
    headers,
  })
  expect(created.ok()).toBeTruthy()
  SPONSOR_ID = (await created.json()).id
  const granted = await request.post(`/api/admin/sponsors/${SPONSOR_ID}/users`, {
    data: { email: SPONSOR_EMAIL },
    headers,
  })
  expect(granted.ok()).toBeTruthy()

  // An idea, not an event: a draft with a placeholder date, opened to
  // sponsorship. Sponsors see it as provisional; students do not see it.
  const day = 24 * 3600 * 1000
  const idea = await request.post('/api/admin/events', {
    data: {
      slug: `e2e-idea-${STAMP}`,
      title: IDEA_TITLE,
      category: 'Socials',
      description: 'A hardware hackathon for the spring.',
      starts_at: new Date(Date.now() + 60 * day).toISOString(),
      ends_at: new Date(Date.now() + 60 * day + 3 * 3600 * 1000).toISOString(),
      published: false,
      sponsorship_claimable: true,
    },
    headers,
  })
  expect(idea.ok()).toBeTruthy()
  IDEA_ID = (await idea.json()).id
})

test.afterAll(async ({ request }) => {
  if (SPONSOR_ID == null && IDEA_ID == null) return
  const csrfToken = await signInAsAdmin(request)
  const headers = { 'X-CSRF-Token': csrfToken }
  if (IDEA_ID != null) await request.delete(`/api/admin/events/${IDEA_ID}`, { headers })
  if (SPONSOR_ID != null) await request.delete(`/api/admin/sponsors/${SPONSOR_ID}`, { headers })
})

test.describe('as the sponsor', () => {
  // Starts signed out in either project, then signs in as the sponsor.
  test.use({ storageState: { cookies: [], origins: [] } })

  test('the dashboard names the package without its wording or a members count', async ({ page }) => {
    test.skip(test.info().project.name !== 'public', 'one sponsor run is enough')

    const login = await page.request.post('/api/auth/dev-login', { data: { email: SPONSOR_EMAIL } })
    expect(login.ok()).toBeTruthy()

    await page.goto('/portal')
    await expect(page.getByRole('heading', { name: 'Your year at a glance.' })).toBeVisible()
    await expect(page.locator('.portal-aside')).toContainText(NAME)

    const labels = page.locator('.ees-stat__label')
    await expect(labels).toHaveText(['Live listings', 'Sponsorable events', 'Marketing shares left'])

    const main = page.locator('.portal-main')
    await expect(main).toContainText('Sponsored workshop')
    await expect(main).toContainText('Marketing to members')
    await expect(main.getByRole('link', { name: 'Ask about this' }).first()).toBeVisible()
    await expect(main).not.toContainText('What we agreed')
    await expect(main).not.toContainText('The Student Group shall')
    await expect(main).not.toContainText('The Sponsor shall')

    // The idea is offered, marked as one, with no date more precise than a month.
    const idea = main.locator('.ees-card').filter({ hasText: IDEA_TITLE }).first()
    await expect(idea).toContainText('Idea · details TBC')
    await expect(idea).toContainText('details to be confirmed')
    await expect(idea).toContainText('A hardware hackathon for the spring.')
    await expect(idea.getByRole('button', { name: 'Register interest' })).toBeVisible()
  })
})

test('the committee sets the standard package and the sponsor sees it', async ({ page, browser }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')

  const before = await standardMarketing(page.request)
  const next = before + 1
  try {
    await page.goto('/admin?tab=sponsors')
    const card = page.locator('.ees-card').filter({ hasText: 'Standard package' }).last()
    await expect(card).toBeVisible()
    const row = card.locator('.package-row').filter({ hasText: 'Marketing to members' })
    await row.getByRole('spinbutton').fill(String(next))
    await row.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByText(`Standard package: ${next} share${next === 1 ? '' : 's'} a year.`)).toBeVisible()

    // It holds across a reload…
    await page.reload()
    const again = page
      .locator('.ees-card').filter({ hasText: 'Standard package' }).last()
      .locator('.package-row').filter({ hasText: 'Marketing to members' })
    await expect(again.getByRole('spinbutton')).toHaveValue(String(next))

    // …and a sponsor on the standard is counted against it.
    const sponsor = await browser.newContext({ storageState: { cookies: [], origins: [] } })
    try {
      const sponsorPage = await sponsor.newPage()
      const login = await sponsorPage.request.post('/api/auth/dev-login', { data: { email: SPONSOR_EMAIL } })
      expect(login.ok()).toBeTruthy()
      await sponsorPage.goto('/portal')
      // StatBlock's root has no class: find the tile by its label.
      const shares = sponsorPage.locator('div:has(> .ees-stat__label:text-is("Marketing shares left")) > .ees-stat__value')
      await expect(shares).toHaveText(`${next} of ${next}`)
    } finally {
      await sponsor.close()
    }
  } finally {
    // The standard is shared by every sponsor on the stack: put it back.
    const csrf = (await page.context().cookies()).find((c) => c.name === 'csrf')?.value
    await page.request.put('/api/admin/sponsor-package/marketing', {
      data: { allowance: before },
      headers: { 'X-CSRF-Token': csrf || '' },
    })
  }
})

test('the committee records a workshop done outside the portal and the sponsor sees it', async ({ page, browser }) => {
  test.skip(test.info().project.name !== 'admin', 'needs the admin storage state')
  const title = `Workshop at their office ${STAMP}`

  await page.goto(`/admin/sponsors/${SPONSOR_ID}`)
  const what = page.getByLabel('Sponsored workshop: record one done outside the portal')
  await what.fill(title)
  await page.locator('form.term__record').filter({ has: what }).getByRole('button', { name: 'Record' }).click()
  await expect(page.getByText('Recorded against Sponsored workshop.')).toBeVisible()
  const use = page.locator('.term__use').filter({ hasText: title })
  await expect(use).toContainText('Recorded by the committee')
  await expect(use.getByRole('button', { name: 'Remove' })).toBeVisible()

  const sponsor = await browser.newContext({ storageState: { cookies: [], origins: [] } })
  try {
    const sponsorPage = await sponsor.newPage()
    const login = await sponsorPage.request.post('/api/auth/dev-login', { data: { email: SPONSOR_EMAIL } })
    expect(login.ok()).toBeTruthy()
    await sponsorPage.goto('/portal')
    const workshop = sponsorPage.locator('.pkg-row').filter({ hasText: 'Sponsored workshop' })
    await expect(workshop).toContainText('1 of 1 used')
    await expect(workshop).toContainText(title)
    await expect(workshop).toContainText('recorded by the committee')
  } finally {
    await sponsor.close()
  }
})
