// Student -> committee moderation -> public listing -> separate email approval.
// Runs against the isolated compose test stack, never production.
const { test, expect } = require('@playwright/test')

const token = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

// These exercises sign in as members and committee via dev-login; the admin
// project already has an admin state, but the flow needs to switch identities.
test.use({ storageState: { cookies: [], origins: [] } })

async function signIn(page, email, makeAdmin = false) {
  const response = await page.request.post('/api/auth/dev-login', {
    data: { email, make_admin: makeAdmin },
  })
  expect(response.ok()).toBeTruthy()
  return await response.json()
}

async function submit(page, title) {
  await page.goto('/opportunities')
  await page.getByRole('button', { name: 'Submit an opportunity' }).click()
  await expect(page.locator('.site-identity')).toBeVisible()
  await page.getByPlaceholder('e.g. Summer research programme').fill(title)
  await page.getByPlaceholder('Describe the opportunity, eligibility, dates and next steps…').fill('An undergraduate research and engineering programme offering hands-on experience at Imperial, with an application form and project workshops.')
  await page.getByPlaceholder('organiser@example.com').fill('organiser@example.org')
  await page.getByLabel('Year 2').check()
  await page.getByRole('button', { name: 'Send for approval' }).click()
  await expect(page.getByRole('status')).toContainText('Submitted for admin review')
  await expect(page.getByText(title)).toBeVisible()
  await expect(page.getByText('pending', { exact: true })).toBeVisible()
}

test('sign-in is required before submitting an opportunity', async ({ page }) => {
  test.skip(test.info().project.name !== 'public', 'one anonymous run is enough')
  await page.goto('/opportunities')
  await expect(page.getByRole('heading', { name: 'Opportunities' })).toBeVisible()
  await page.getByRole('button', { name: 'Submit an opportunity' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
})

test('rejection feedback lets the owner revise only when an admin allows it', async ({ page }) => {
  test.skip(test.info().project.name !== 'public', 'one moderation run is enough')
  const member = `opp-member-reject-${token}@example.com`
  const title = `Rejected opportunity ${token}`
  await signIn(page, member)
  await submit(page, title)

  await signIn(page, `opp-admin-reject-${token}@example.com`, true)
  await page.goto('/admin?tab=overview')
  await expect(page.getByRole('heading', { name: /Opportunities awaiting approval/ })).toBeVisible()
  await page.getByRole('button', { name: 'Review opportunities' }).click()
  const listing = page.locator('.opp-admin .ees-card').filter({ hasText: title })
  await expect(listing).toBeVisible()
  await listing.getByPlaceholder('Notes for the submitter if rejected, or feedback on approval…').fill('Please provide an organiser contact and application requirements.')
  await listing.getByLabel('Let the submitter edit and resubmit this opportunity after rejection').check()
  page.once('dialog', (dialog) => dialog.accept())
  await listing.getByRole('button', { name: 'Reject' }).click()
  await expect(listing).toBeHidden()

  await signIn(page, member)
  await page.goto('/opportunities')
  await page.getByRole('button', { name: 'My submissions' }).click()
  await expect(page.getByText('rejected', { exact: true })).toBeVisible()
  await expect(page.getByText('Please provide an organiser contact and application requirements.')).toBeVisible()
  await page.getByRole('button', { name: 'Edit & resubmit' }).click()
  await expect(page.getByRole('heading', { name: 'Edit rejected opportunity' })).toBeVisible()
  await page.getByPlaceholder('Describe the opportunity, eligibility, dates and next steps…').fill('This updated opportunity now explains eligibility, organiser details, deadlines and what applicants need to prepare.')
  await page.getByRole('button', { name: 'Resubmit for approval' }).click()
  await expect(page.getByRole('status')).toContainText('Submitted for admin review')
  await expect(page.getByText('pending', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Browse opportunities' }).click()
  await expect(page.getByText(title)).toHaveCount(0)
})

test('owner drafts and approves the email before it reaches the admin queue', async ({ page }) => {
  test.skip(test.info().project.name !== 'public', 'one moderation run is enough')
  const member = `opp-member-approve-${token}@example.com`
  const title = `Approved opportunity ${token}`
  await signIn(page, member)
  await submit(page, title)

  await signIn(page, `opp-admin-approve-${token}@example.com`, true)
  await page.goto('/admin?tab=opportunities')
  const listing = page.locator('.opp-admin .ees-card').filter({ hasText: title })
  await expect(listing).toBeVisible()
  page.once('dialog', (dialog) => dialog.accept())
  await listing.getByRole('button', { name: 'Approve & publish' }).click()
  await expect(listing).toBeHidden()
  // No email is created until the submitter explicitly approves their words.
  await page.goto('/admin?tab=email')
  await expect(page.getByText(`Opportunity: ${title}`, { exact: true })).toHaveCount(0)

  await signIn(page, member)
  await page.goto('/opportunities')
  await expect(page.getByText(title).first()).toBeVisible()
  await page.getByRole('button', { name: /My submissions/ }).click()
  await page.getByRole('button', { name: 'Draft announcement email' }).click()
  await expect(page.getByRole('heading', { name: 'Draft your opportunity announcement' })).toBeVisible()
  const draftBody = 'We are pleased to announce a new research opportunity for year two students. Applications and full details are available at the linked programme page.'
  await page.getByPlaceholder('Describe what makes the opportunity worth sharing…').fill(draftBody)
  await page.getByRole('button', { name: 'Save draft' }).click()
  await expect(page.getByRole('status')).toContainText('Draft saved')
  await page.getByRole('button', { name: 'Approve & send to admins' }).click()
  await expect(page.getByRole('status')).toContainText('awaiting final approval')

  await signIn(page, `opp-admin-approve-${token}@example.com`, true)
  await page.goto('/admin?tab=email')
  await expect(page.getByRole('heading', { name: /Email queue/ })).toBeVisible()
  await expect(page.getByText(`Opportunity: ${title}`).first()).toBeVisible()
  await expect(page.getByText('needs approval', { exact: true }).first()).toBeVisible()
  // Never click Send: committee email approval remains a separate step.
})
