// A ticket is a public, token-linked page. It must render the actual ticket,
// not just the shared navbar/footer when the route is opened directly.
const { test, expect } = require('@playwright/test')

const TOKEN = 'fixture-ticket-token'
const TICKET = {
  token: TOKEN,
  event: { id: 42, title: 'E2E ticket event', date: 'Mon 2 Nov', time: '19:00', venue: 'EEE Building' },
  holder: 'Test Attendee',
  ticketType: 'Standard',
  quantity: 1,
  status: 'confirmed',
  statusLabel: 'Confirmed',
  valid: true,
  checkedInAt: null,
  qrUrl: `/api/qr/${TOKEN}.png`,
  mayCheckIn: false,
}

const PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/qk0AAAAASUVORK5CYII=', 'base64')

async function stubSite(page) {
  await page.route('**/api/auth/session', route => route.fulfill({ json: { authenticated: false } }))
  await page.route('**/api/status', route => route.fulfill({ json: { features: {}, unavailable: [], notice: null } }))
}

test('show your ticket renders the event, ticket and QR on a direct visit', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await stubSite(page)
  await page.route(`**/api/tickets/${TOKEN}`, route => route.fulfill({ json: TICKET }))
  await page.route(`**/api/qr/${TOKEN}.png`, route => route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL }))

  await page.goto(`/ticket/${TOKEN}`)
  const ticket = page.locator('main.ticket-page')
  await expect(ticket.getByRole('heading', { name: TICKET.event.title })).toBeVisible()
  await expect(ticket.getByText(TICKET.holder)).toBeVisible()
  await expect(ticket.getByText(TICKET.ticketType)).toBeVisible()
  await expect(ticket.getByText('Confirmed')).toBeVisible()
  await expect(ticket.getByRole('img', { name: 'Ticket QR code' })).toHaveAttribute('src', TICKET.qrUrl)
  await expect.poll(() => ticket.getByRole('img', { name: 'Ticket QR code' }).evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true)
  await expect(page).toHaveTitle(/E2E ticket event.*Your ticket/)
  expect(errors).toEqual([])
})

test('invalid ticket links show an error rather than an empty page', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await stubSite(page)
  await page.route(`**/api/tickets/${TOKEN}`, route => route.fulfill({ status: 404, json: { detail: 'Ticket not found' } }))

  await page.goto(`/ticket/${TOKEN}`)
  await expect(page.locator('main.ticket-page .error')).toHaveText('Ticket not found')
  expect(errors).toEqual([])
})
