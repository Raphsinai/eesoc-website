// A printed QR opens a public album. Regressions in its Vue setup used to
// render only the shared navigation/footer, with no photo content at all.
const { test, expect } = require('@playwright/test')

const TOKEN = 'fixture-album-token'
const ALBUM = {
  title: 'Halloween Party',
  count: 2,
  downloadUrl: `/api/photos/${TOKEN}/download.zip`,
  printUrl: `/api/photos/${TOKEN}/print.png`,
  images: [
    { id: 1, url: '/test-photo-1.jpg', filename: 'photo-1.jpg' },
    { id: 2, url: '/test-photo-2.jpg', filename: 'photo-2.jpg' },
  ],
}

// A 1x1 PNG is enough to check that each image really loads in the browser.
const PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/qk0AAAAASUVORK5CYII=', 'base64')

async function stubPublicSite(page) {
  await page.route('**/api/auth/session', route => route.fulfill({ json: { authenticated: false } }))
  await page.route('**/api/status', route => route.fulfill({ json: { features: {}, unavailable: [], notice: null } }))
  await page.route('**/test-photo-*.jpg', route => route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL }))
}

test('public QR gallery renders photos, title and download controls', async ({ page }) => {
  const errors = []
  page.on('pageerror', err => errors.push(err.message))
  await stubPublicSite(page)
  await page.route(`**/api/photos/${TOKEN}`, route => route.fulfill({ json: ALBUM }))

  await page.goto(`/photos/${TOKEN}`)
  await expect(page.getByRole('heading', { name: 'Halloween Party' })).toBeVisible()
  await expect(page.locator('.photo-gallery__photo img')).toHaveCount(2)
  for (const image of await page.locator('.photo-gallery__photo img').all()) {
    await expect.poll(() => image.evaluate(el => el.complete && el.naturalWidth > 0)).toBe(true)
  }
  await expect(page.getByRole('link', { name: /Download all 2 photos/ }))
    .toHaveAttribute('href', ALBUM.downloadUrl)
  await expect(page.getByRole('link', { name: 'View printed collage' }))
    .toHaveAttribute('href', ALBUM.printUrl)
  expect(errors).toEqual([])
})

test('unavailable photo links explain why the gallery is empty', async ({ page }) => {
  await stubPublicSite(page)
  await page.route(`**/api/photos/${TOKEN}`, route => route.fulfill({ status: 404, json: { detail: 'Not found' } }))
  await page.goto(`/photos/${TOKEN}`)
  await expect(page.getByRole('alert')).toHaveText('This photo link is unavailable or has been withdrawn.')
})
