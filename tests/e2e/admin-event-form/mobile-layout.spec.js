// Mobile new-event layout regression. The admin frame clips horizontal
// overflow by design, so checking only document.scrollWidth misses fields that
// have actually disappeared off the right edge. Mock only API reads: this
// exercises the real form and its permission-aware rendering without writes.
const { test, expect } = require('@playwright/test')

test.use({ storageState: { cookies: [], origins: [] } })

// Deliberately unsorted, fully populated admin rows. An empty list hid the
// original mobile clipping defect, which came from the event cards rather
// than the event editor controls.
const MOCK_EVENTS = [4, 2, 8, 1, 6, 3, 7, 5].map(n => ({
  id: n,
  slug: `mock-event-${n}`,
  title: n === 8 ? 'Mock event 8: A really long description of this event for layout testing' : `Mock event ${n}`,
  category: 'Social',
  starts_at: `2030-10-${String(n).padStart(2, '0')}T18:00:00Z`,
  published: n % 2 === 0,
  ticket_types: [{ name: 'General admission, with or without refreshments' }],
  taken: 5,
  capacity: 100,
  sponsors: ['A Very Long Sponsor Name To Test Wrapping'],
  can_badge: true,
  can_waitlist: true,
  can_publish: true,
  can_book: true,
  can_edit: true,
  can_delete: true,
  waitlist_enabled: true,
}))

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
    if (path === '/api/admin/events') result = MOCK_EVENTS
    if (path === '/api/admin/dashboard') result = {
      warnings: [], counts: {}, needsAttention: [], recentBookings: [],
      syncRuns: [], storage: { files: 0, bytes: 0 },
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(result) })
  })
}

async function expectContained(page, width, stage) {
  const measurements = await page.locator('.admin-cols--wide').evaluate(card => {
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
  expect(measurements.escaped, `${width}px (${stage}): clipped event list or form controls`).toEqual([])
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


test('admin events are newest-first, 3 per page and searchable without losing access to older events', async ({ page }) => {
  await mockEventEditor(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/admin?tab=events')
  const cards = page.getByTestId('admin-event-card')
  const pages = page.getByRole('navigation', { name: 'Event list pages' })
  const labels = () => cards.locator('.admin-event-card__summary strong').allTextContents()

  await expect(cards).toHaveCount(3)
  expect((await labels()).map(x => x.match(/Mock event (\d+)/)[1])).toEqual(['8', '7', '6'])
  await expect(page.getByTestId('admin-events-range')).toHaveText('1–3 of 8')
  await expect(pages.getByRole('button', { name: 'Previous' })).toBeDisabled()
  await expectContained(page, 390, 'first page populated with event cards')

  await pages.getByRole('button', { name: 'Next' }).click()
  expect((await labels()).map(x => x.match(/Mock event (\d+)/)[1])).toEqual(['5', '4', '3'])
  await expect(page.getByTestId('admin-events-range')).toHaveText('4–6 of 8')
  await expectContained(page, 390, 'second page')

  await pages.getByRole('button', { name: 'Next' }).click()
  expect((await labels()).map(x => x.match(/Mock event (\d+)/)[1])).toEqual(['2', '1'])
  await expect(pages.getByRole('button', { name: 'Next' })).toBeDisabled()
  await expect(page.getByTestId('admin-events-range')).toHaveText('7–8 of 8')
  await cards.last().getByRole('button', { name: 'Edit' }).click()
  await expect(page.getByRole('heading', { name: 'Edit event' })).toBeVisible()
  await expectContained(page, 390, 'editor of an older event')

  // Filtering must search all events, not merely the current three.
  await page.getByRole('textbox', { name: 'Find an event' }).fill('mock-event-4')
  await expect(cards).toHaveCount(1)
  expect((await labels())[0]).toBe('Mock event 4')
  await expect(page.getByTestId('admin-events-range')).toHaveText('1–1 of 1 matches')
  await expect(pages).toHaveCount(0)
  await expectContained(page, 390, 'filtered results')

  await page.getByRole('textbox', { name: 'Find an event' }).fill('not-a-real-event')
  await expect(cards).toHaveCount(0)
  await expect(page.getByText('No matching events.')).toBeVisible()
  await page.getByRole('textbox', { name: 'Find an event' }).clear()
  await expect(cards).toHaveCount(3)
  expect((await labels()).map(x => x.match(/Mock event (\d+)/)[1])).toEqual(['8', '7', '6'])
})
