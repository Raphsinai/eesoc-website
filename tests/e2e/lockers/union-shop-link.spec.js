// Locker purchases must go to the specific EEE locker product, not the Union homepage.
const { test, expect } = require('@playwright/test')

const SHOP_URL = 'https://www-s.imperialcollegeunion.org/shop/product/2771-/'

// Keep these tests independent of whether booking is currently paused in the
// test stack, and of any imported Union sales or membership data.
async function mockStatus(page, paused = false) {
  await page.route('**/api/status', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      features: paused ? { lockers: { reason: 'Booking paused' } } : {},
      unavailable: paused ? ['lockers'] : [],
      notice: null,
    }),
  }))
}

test('booking page links directly to the official EEE locker item', async ({ page }) => {
  await mockStatus(page)
  await page.goto('/lockers')

  const link = page.getByRole('link', { name: /EEE locker on the Union Shop/ })
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', SHOP_URL)
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
})

test('members without an available purchase get a shop link by the booking controls', async ({ page }) => {
  await mockStatus(page)
  await page.route('**/api/auth/session', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ authenticated: true, is_member: true, user: { email: 'member@test.invalid' } }),
  }))
  await page.route('**/api/lockers', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ floors: [], entitlement: { purchased: 0, remaining: 0 } }),
  }))
  await page.goto('/lockers')

  const link = page.getByRole('link', { name: 'Buy a locker on the Union Shop ↗' })
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', SHOP_URL)
})

test('paused booking does not direct students to buy a locker yet', async ({ page }) => {
  await mockStatus(page, true)
  await page.goto('/lockers')

  await expect(page.getByText('Locker booking is not open yet', { exact: true })).toBeVisible()
  await expect(page.locator('a[href="' + SHOP_URL + '"]')).toHaveCount(0)
})
