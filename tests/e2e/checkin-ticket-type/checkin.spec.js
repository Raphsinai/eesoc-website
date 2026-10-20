// The door check-in screen (CheckinView.vue, route /checkin/:eventId) makes
// the person's name and ticket type obvious at arm's length: large and bold in
// the scan result panel, a coloured tag on every list row, and the ticket type
// named in the unpaid confirm() prompt.
//
// Everything the view fetches is stubbed with page.route, so these specs need
// no seeded data, no camera and no backend: they run identically against a
// static `vite preview` build in BOTH Playwright projects (public + admin),
// because the session stub returns is_admin: true and the /checkin guard only
// checks hasAdminArea (isAdmin || committeeLogin).
const { test, expect } = require('@playwright/test')

// A phone at the door, not a desk: the exact viewport the "readable at arm's
// length" acceptance criteria are written against.
test.use({ viewport: { width: 390, height: 844 } })

const EVENT_ID = 42
const EVENT_URL = `/checkin/${EVENT_ID}`
// The state poll echoes this fingerprint back, or the component sees a change
// and reloads the event on every 2s tick forever.
const BOOKINGS = 'fp1'

const EVENT = {
  id: EVENT_ID,
  title: 'E2E door event',
  date: 'Wed 8 Oct',
  time: '19:00',
  venue: 'EEE foyer',
}

// The full attendee shape CheckinView reads. Everything the meta line and the
// badges key off (paid, status, guest, committeeAdded) is explicit so a row
// renders exactly the tags a test expects and no extras.
function makeAttendee(overrides = {}) {
  return {
    id: 1,
    name: 'Ada Lovelace',
    email: 'ada.lovelace@example.com',
    shortcode: 'ada22',
    orderNumber: 'E2E-001',
    ticketType: 'Member',
    quantity: 1,
    paid: true,
    status: 'confirmed',
    eventId: EVENT_ID,
    checkedInAt: null,
    checkedInBy: null,
    guest: false,
    committeeAdded: false,
    ...overrides,
  }
}

// Register the boot probes and the two check-in endpoints. scan/reservation are
// optional because not every test exercises both.
async function stubApi(page, { attendees, scan, reservation }) {
  // Boot: the guard's refresh() and App's refreshStatus() run once on load.
  // The guard is action-based. Supply the exact check-in grants, as a real
  // committee session does; is_admin alone does NOT authorise any button.
  await page.route('**/api/auth/session', (route) =>
    route.fulfill({ json: {
      authenticated: true, is_admin: true,
      capabilities: ['events.scope.all', 'events.checkin.view',
        'events.checkin.scan', 'events.checkin.manual',
        'events.checkin.override', 'events.checkin.undo'],
    } }))
  await page.route('**/api/status', (route) =>
    route.fulfill({ json: { features: {}, unavailable: [], notice: null } }))

  // Register /state before the plain event path: both are under the same stem
  // but are distinct URLs, and /state's fingerprint must match the event load.
  await page.route(`**/api/checkin/events/${EVENT_ID}/state`, (route) =>
    route.fulfill({ json: { checkins: [], bookings: BOOKINGS } }))
  await page.route(`**/api/checkin/events/${EVENT_ID}`, (route) =>
    route.fulfill({ json: { event: EVENT, attendees, bookings: BOOKINGS } }))

  if (scan) {
    await page.route('**/api/checkin/scan', (route) => route.fulfill({ json: scan }))
  }
  if (reservation) {
    await page.route('**/api/checkin/reservations/*', (route) =>
      route.fulfill({ json: reservation }))
  }
}

// The scanner needs a REAL MediaStream: CheckinView's decode() returns null
// unless the <video>'s readyState reaches 2, which only happens with a stream
// attached. A canvas captureStream is one, and the interval keeps frames
// flowing so readyState climbs and stays there. BarcodeDetector is stubbed so
// detect() always returns the same code, which the view POSTs to /checkin/scan;
// the route stub decides the outcome.
async function stubCamera(page) {
  await page.addInitScript(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 64
    canvas.height = 64
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#333'
    ctx.fillRect(0, 0, 64, 64)
    const stream = canvas.captureStream(30)
    setInterval(() => {
      ctx.fillRect(0, 0, 64, 64)
    }, 100)
    navigator.mediaDevices.getUserMedia = async () => stream
    window.BarcodeDetector = class {
      async detect() {
        return [{ rawValue: 'QR-STUB' }]
      }
    }
  })
}

function fontSize(locator) {
  return locator.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))
}

function fontWeight(locator) {
  return locator.evaluate((el) => Number(getComputedStyle(el).fontWeight))
}

// A box is "not clipped" when it sits fully inside its container; the small
// epsilon absorbs sub-pixel rounding in the layout engine.
function within(inner, outer) {
  if (!inner || !outer) return false
  return (
    inner.x >= outer.x - 0.5 &&
    inner.y >= outer.y - 0.5 &&
    inner.x + inner.width <= outer.x + outer.width + 0.5 &&
    inner.y + inner.height <= outer.y + outer.height + 0.5
  )
}

function withinViewport(box, viewport) {
  if (!box || !viewport) return false
  return (
    box.x >= -0.5 &&
    box.y >= -0.5 &&
    box.x + box.width <= viewport.width + 0.5 &&
    box.y + box.height <= viewport.height + 0.5
  )
}

test('a checked-in scan shows the name and ticket type large and unclipped', async ({ page }) => {
  await stubCamera(page)
  await stubApi(page, {
    attendees: [],
    scan: { outcome: 'checked_in', attendee: makeAttendee() },
  })

  await page.goto(EVENT_URL)
  await page.getByRole('button', { name: 'Scan tickets' }).click()

  const panel = page.locator('.scan__result')
  // The scan loop ticks every 120ms and there is a 4s same-code guard that does
  // not apply to a first scan; this just needs to survive the loop's first POST.
  await expect(panel).toBeVisible({ timeout: 15000 })

  const name = page.locator('.scan__name')
  const ticket = page.locator('.scan__ticket')
  await expect(name).toHaveText('Ada Lovelace')
  await expect(ticket).toHaveText('Member')

  // Readable at arm's length: the name large and bold, the ticket type still a
  // prominent element in its own right beneath it.
  expect(await fontSize(name)).toBeGreaterThanOrEqual(32)
  expect(await fontWeight(name)).toBeGreaterThanOrEqual(600)
  expect(await fontSize(ticket)).toBeGreaterThanOrEqual(20)

  // Neither element may spill out of the square camera frame, nor off the
  // phone screen.
  const panelBox = await panel.boundingBox()
  const nameBox = await name.boundingBox()
  const ticketBox = await ticket.boundingBox()
  const viewport = page.viewportSize()
  expect(within(nameBox, panelBox)).toBe(true)
  expect(within(ticketBox, panelBox)).toBe(true)
  expect(withinViewport(nameBox, viewport)).toBe(true)
  expect(withinViewport(ticketBox, viewport)).toBe(true)
})

test('a multi-person scan shows "× 3 people" in the panel', async ({ page }) => {
  await stubCamera(page)
  await stubApi(page, {
    attendees: [],
    scan: {
      outcome: 'checked_in',
      attendee: makeAttendee({ name: 'Ada Lovelace', ticketType: 'Member', quantity: 3 }),
    },
  })

  await page.goto(EVENT_URL)
  await page.getByRole('button', { name: 'Scan tickets' }).click()

  const panel = page.locator('.scan__result')
  await expect(panel).toBeVisible({ timeout: 15000 })
  await expect(panel).toContainText('× 3 people')
})

test('an "already in" scan still shows the name and ticket type', async ({ page }) => {
  await stubCamera(page)
  await stubApi(page, {
    attendees: [],
    scan: {
      outcome: 'already',
      attendee: makeAttendee({ name: 'Grace Hopper', ticketType: 'Standard' }),
    },
  })

  await page.goto(EVENT_URL)
  await page.getByRole('button', { name: 'Scan tickets' }).click()

  await expect(page.locator('.scan__result')).toBeVisible({ timeout: 15000 })
  await expect(page.locator('.scan__headline')).toHaveText('Already in')
  await expect(page.locator('.scan__name')).toHaveText('Grace Hopper')
  await expect(page.locator('.scan__ticket')).toHaveText('Standard')
})

test('a "not paid" scan still shows the name and ticket type', async ({ page }) => {
  await stubCamera(page)
  await stubApi(page, {
    attendees: [],
    scan: {
      outcome: 'unpaid',
      attendee: makeAttendee({ name: 'Katherine Johnson', ticketType: 'Member' }),
    },
  })

  await page.goto(EVENT_URL)
  await page.getByRole('button', { name: 'Scan tickets' }).click()

  await expect(page.locator('.scan__result')).toBeVisible({ timeout: 15000 })
  await expect(page.locator('.scan__headline')).toHaveText('Not paid')
  await expect(page.locator('.scan__name')).toHaveText('Katherine Johnson')
  await expect(page.locator('.scan__ticket')).toHaveText('Member')
})

test('checking in from the list shows the panel above the list and tap dismisses it', async ({ page }) => {
  const ada = makeAttendee({ name: 'Ada Lovelace', ticketType: 'Member' })
  await stubApi(page, {
    attendees: [ada],
    // checked_in, but with no checkedInAt merged in, so the row stays visible
    // in the "not in" list while the panel is being asserted against it.
    reservation: { outcome: 'checked_in', attendee: ada },
  })

  await page.goto(EVENT_URL)

  // The camera never opens this whole test: the confirmation must not need it.
  await expect(page.getByRole('button', { name: 'Scan tickets' })).toBeVisible()

  await page.getByRole('button', { name: 'Check in' }).click()

  const panel = page.locator('.scan__result')
  await expect(panel).toBeVisible()
  await expect(page.locator('.scan__name')).toHaveText('Ada Lovelace')
  await expect(page.locator('.scan__ticket')).toHaveText('Member')

  // The panel lives in the .scan area above the list — not over a live video,
  // because no camera frame exists while the camera is closed.
  await expect(page.locator('.scan__frame')).toHaveCount(0)
  await expect(page.locator('.scan .scan__result')).toHaveCount(1)
  const panelBox = await panel.boundingBox()
  const firstRowBox = await page.locator('.person').first().boundingBox()
  expect(panelBox).not.toBeNull()
  expect(firstRowBox).not.toBeNull()
  expect(panelBox.y + panelBox.height).toBeLessThanOrEqual(firstRowBox.y + 0.5)

  // Tap to dismiss.
  await panel.click()
  await expect(page.locator('.scan__result')).toHaveCount(0)
})

test('each list row shows the ticket type as a non-neutral badge', async ({ page }) => {
  await stubApi(page, {
    attendees: [
      makeAttendee({ id: 1, name: 'Ada Lovelace', ticketType: 'Member' }),
      makeAttendee({ id: 2, name: 'Grace Hopper', ticketType: 'Industry' }),
    ],
  })

  await page.goto(EVENT_URL)

  const people = page.locator('.person')
  await expect(people).toHaveCount(2)

  const ticketTypes = ['Member', 'Industry']
  for (let i = 0; i < ticketTypes.length; i++) {
    const badge = people.nth(i).locator('.person__tags .ees-badge')
    await expect(badge).toHaveText(ticketTypes[i])
    // A real call-out, not the grey neutral tone — the neutral badge is what the
    // meta line already is, so a neutral tag would be the type hiding in plain
    // sight a second time.
    await expect(badge).not.toHaveClass(/ees-badge--neutral/)
  }
})

test('an unpaid row asks with the ticket type in the confirm message', async ({ page }) => {
  const unpaid = makeAttendee({ name: 'Ada Lovelace', ticketType: 'Member', paid: false })
  await stubApi(page, {
    attendees: [unpaid],
    // The POST fires only after the prompt is accepted; stub it regardless so
    // accepting never 404s mid-test.
    reservation: { outcome: 'checked_in', attendee: unpaid },
  })

  await page.goto(EVENT_URL)

  let promptType = null
  let promptMessage = ''
  page.once('dialog', (dialog) => {
    promptType = dialog.type()
    promptMessage = dialog.message()
    dialog.accept()
  })

  await page.getByRole('button', { name: 'Check in' }).click()

  // The person at the door must be able to tell a Member from a Sponsor from
  // the prompt alone, without opening the row.
  await expect.poll(() => promptMessage).toContain('Member')
  expect(promptType).toBe('confirm')
})