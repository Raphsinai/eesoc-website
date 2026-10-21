// Mobile new-event layout regression. The admin frame clips horizontal
// overflow by design, so checking only document.scrollWidth misses fields that
// have actually disappeared off the right edge. Mock only API reads: this
// exercises the real form and its permission-aware rendering without writes.
const { test, expect } = require('@playwright/test')

test.use({ storageState: { cookies: [], origins: [] } })

async function mockEventEditor(page) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    let result = []
    if (path === '/api/auth/session') result = {
      authenticated: true,
      is_admin: true,
      role: 'admin',
      user: { email: 'mobile-admin@test.invalid' },
      capabilities: [
        'events.view', 'events.create', 'events.edit', 'events.scope.all',
        'events.ticket_types.create', 'events.ticket_types.edit',
        'events.ticket_types.delete', 'events.publish',
      ],
    }
    if (path === '/api/status') result = { features: {}, unavailable: [], notice: null }
    if (path === '/api/admin/dashboard') result = {
      warnings: [], counts: {}, needsAttention: [], recentBookings: [],
      syncRuns: [], storage: { files: 0, bytes: 0 },
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(result) })
  })
}

async function expectContained(page, width, stage) {
  const measurements = await page.locator('.event-editor').evaluate(card => {
    const viewport = document.documentElement.clientWidth
    const nodes = [card, ...card.querySelectorAll('*')]
    const escaped = nodes.filter(el => {
      const style = getComputedStyle(el)
      if (style.display === 'none' || style.visibility === 'hidden') return false
      const rect = el.getBoundingClientRect()
      return rect.width > 0 && (rect.right > viewport + 1 || rect.left < -1)
    }).map(el => ({
      tag: el.tagName,
      className: typeof el.className === 'string' ? el.className : '',
      text: el.textContent?.trim().slice(0, 40),
      right: Math.round(el.getBoundingClientRect().right),
    }))
    return { viewport, pageWidth: document.documentElement.scrollWidth, escaped }
  })
  expect(measurements.viewport, `${width}px (${stage}): viewport width`).toBe(width)
  expect(measurements.pageWidth, `${width}px (${stage}): page overflow`).toBeLessThanOrEqual(width)
  expect(measurements.escaped, `${width}px (${stage}): clipped form controls`).toEqual([])
}

test('new event, ticket types and sales dates all remain inside phone and tablet width', async ({ page }) => {
  await mockEventEditor(page)
  for (const width of [320, 360, 390, 414, 768, 1024]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/admin?tab=events')
    await expect(page.getByRole('heading', { name: 'New event' })).toBeVisible()
    await expectContained(page, width, 'empty editor')

    await page.getByRole('button', { name: 'Add ticket type' }).click()
    await expect(page.locator('.event-editor__ticket-fields')).toHaveCount(1)
    await expectContained(page, width, 'ticket fields')

    await page.getByRole('button', { name: 'Add ticket type' }).click()
    await page.getByText('On sale from a set date', { exact: true }).click()
    await expect(page.locator('.event-editor input[type="datetime-local"]')).toHaveCount(4)
    await expectContained(page, width, 'scheduled ticket sales')

    await page.getByTestId('ticket-windows').check()
    await page.getByTestId('ticket-window-0').getByText('Goes on sale later than the event\'s sale date').click()
    await page.getByTestId('ticket-window-0').getByText('Stops selling at a set time').click()
    await expectContained(page, width, 'expanded ticket sales windows')
  }
})
