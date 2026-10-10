// Passkeys in a real browser: Chromium's own WebAuthn stack, answering through
// a CDP virtual authenticator — the same code path as Touch ID or a phone,
// with the fingerprint pressed automatically.
//
// Covers the three ways a passkey is made or used: offered after a code
// sign-in, then used to sign in with no code; made through a committee
// link; and removed from the account page.
const { test, expect } = require('@playwright/test')

const { fetchLatestCode } = require('../helpers/mailpit')

// Every flow here starts signed out (or signs its own admin in), so neither
// project's storage state applies.
test.use({ storageState: { cookies: [], origins: [] } })

function unique(label) {
  return `e2e-passkey-${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`
}

/** A platform authenticator with a resident-key store and a "fingerprint" that
 *  always matches, attached to this page's browser. */
async function addAuthenticator(page) {
  const client = await page.context().newCDPSession(page)
  await client.send('WebAuthn.enable')
  const { authenticatorId } = await client.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  })
  return {
    async credentials() {
      const { credentials } = await client.send('WebAuthn.getCredentials', { authenticatorId })
      return credentials
    },
  }
}

// How many messages mailpit holds for an address. A second sign-in for the
// same address must wait for its *new* code: reading the newest message too
// early returns the first sign-in's, already used.
async function messagesTo(email) {
  const res = await fetch('http://localhost:8025/api/v1/messages?limit=200')
  const { messages = [] } = await res.json()
  return messages.filter((m) =>
    (m.To || []).some((r) => (r.Address || '').toLowerCase() === email.toLowerCase()),
  ).length
}

async function signInWithCode(page, email) {
  const before = await messagesTo(email)
  await page.getByRole('button', { name: 'Log in' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByPlaceholder('abc123@ic.ac.uk').fill(email)
  await dialog.getByRole('button', { name: 'Email me a code' }).click()
  await expect.poll(() => messagesTo(email), { timeout: 30000 }).toBeGreaterThan(before)
  const code = await fetchLatestCode({ to: email })
  await dialog.getByPlaceholder('123456').fill(code)
  await dialog.getByRole('button', { name: 'Log in', exact: true }).click()
  return dialog
}

test('save a passkey after a code sign-in, then sign in with it alone', async ({ page }) => {
  const authenticator = await addAuthenticator(page)
  const email = unique('offer')
  await page.goto('/')

  const dialog = await signInWithCode(page, email)

  // A brand-new external address is asked its name first. (The design
  // system's Field does not tie its label to the input, hence autocomplete.)
  await dialog.locator('input[autocomplete="given-name"]').fill('Ada')
  await dialog.getByRole('button', { name: 'Done' }).click()

  // Then offered a passkey, since the account has none.
  await expect(dialog.getByRole('button', { name: 'Save a passkey' })).toBeVisible()
  await dialog.getByRole('button', { name: 'Save a passkey' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(await authenticator.credentials()).toHaveLength(1)

  // Out, and back in with no address and no code.
  await page.getByRole('button', { name: 'Log out' }).click()
  await page.getByRole('button', { name: 'Log in' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Sign in with a passkey' }).click()
  await expect(page.getByRole('button', { name: 'Log out' })).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // The account page lists it, and removing it works.
  await page.getByRole('link', { name: /Ada/ }).click()
  await expect(page).toHaveURL(/\/account$/)
  await expect(page.getByText(/Added .*last used/)).toBeVisible()
  page.once('dialog', (d) => d.accept())
  await page.getByRole('button', { name: 'Remove' }).click()
  await expect(page.getByText('No passkeys yet')).toBeVisible()
})

test('a code sign-in with a passkey already saved is not offered another', async ({ page }) => {
  await addAuthenticator(page)
  const email = unique('no-reoffer')
  await page.goto('/')

  let dialog = await signInWithCode(page, email)
  // Named now, so the second sign-in has no reason to ask anything.
  await dialog.locator('input[autocomplete="given-name"]').fill('Grace')
  await dialog.getByRole('button', { name: 'Done' }).click()
  await dialog.getByRole('button', { name: 'Save a passkey' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await page.getByRole('button', { name: 'Log out' }).click()
  dialog = await signInWithCode(page, email)
  // Signed in, dialog gone: no offer, because they have one.
  await expect(page.getByRole('button', { name: 'Log out' })).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('a committee-made link signs a sponsor in with a passkey and no code', async ({ browser }) => {
  // The committee side: an admin makes the link on the People tab.
  const adminContext = await browser.newContext()
  const admin = await adminContext.request.post('/api/auth/dev-login', {
    data: { email: unique('admin'), make_admin: true },
  })
  expect(admin.status()).toBe(200)
  const adminPage = await adminContext.newPage()
  await adminPage.goto('/admin?tab=people')

  const sponsor = unique('sponsor')
  await adminPage.getByPlaceholder('name@company.com').fill(sponsor)
  await adminPage.getByRole('button', { name: 'Make a link' }).click()
  const linkField = adminPage.getByLabel('Passkey setup link')
  await expect(linkField).toBeVisible()
  const url = await linkField.inputValue()
  expect(url).toMatch(/\/passkey-setup#.{20,}/)

  // The sponsor side: a different browser, never signed in, no code sent.
  const sponsorContext = await browser.newContext()
  const page = await sponsorContext.newPage()
  const authenticator = await addAuthenticator(page)
  await page.goto(url)

  await expect(page.getByRole('heading', { name: 'Set up a passkey' })).toBeVisible()
  await expect(page.getByText(sponsor)).toBeVisible()
  await page.getByRole('button', { name: 'Save a passkey and sign in' }).click()

  await expect(page.getByRole('heading', { name: "You're signed in" })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Log out' })).toBeVisible()
  expect(await authenticator.credentials()).toHaveLength(1)
  // The token leaves the address bar once spent.
  await expect(page).toHaveURL(/\/passkey-setup$/)

  // The link is single-use: opening it again says so.
  const again = await sponsorContext.newPage()
  await again.goto(url)
  await expect(again.getByRole('heading', { name: "This link can't be used" })).toBeVisible()

  // And the admin's list shows it used.
  await adminPage.reload()
  await expect(adminPage.getByText(sponsor).first()).toBeVisible()
  await expect(adminPage.getByText(/^used/).first()).toBeVisible()

  await adminContext.close()
  await sponsorContext.close()
})
