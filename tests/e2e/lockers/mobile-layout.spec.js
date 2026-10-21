const { test, expect } = require('@playwright/test')

for (const width of [320, 360, 390, 414]) {
  test(`phone ${width}px: pages do not widen the viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    for (const route of ['/', '/opportunities', '/lockers', '/events', '/resources', '/sponsors']) {
      await page.goto(route)
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
      await expect(page.locator('.site-header')).toBeVisible()
    }
  })
}

test('locker map shows persistent scroll control and keeps it synchronized with the wall', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/status', route => route.fulfill({ json: { features: {}, unavailable: [], notice: null } }))
  await page.goto('/lockers')
  const wall = page.locator('.locker-wall__blocks').first()
  const range = page.getByRole('slider', { name: /Scroll Level .* lockers horizontally/ }).first()
  await expect(wall).toBeVisible()
  await expect(range).toBeVisible()
  await expect(page.getByText('Swipe sideways to see all lockers ↔').first()).toBeVisible()
  const max = Number(await range.getAttribute('max'))
  expect(max).toBeGreaterThan(0)
  await range.fill(String(Math.floor(max / 2)))
  await expect.poll(() => wall.evaluate(el => el.scrollLeft)).toBeGreaterThan(0)
  await wall.evaluate(el => { el.scrollLeft = el.scrollWidth })
  await expect.poll(async () => Number(await range.inputValue())).toBe(max)
})
