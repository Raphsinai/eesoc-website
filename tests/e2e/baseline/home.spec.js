// The home page renders its hero and the sections that live on it.
//
// These are the page's own copy, not the API's, so this spec stays green even
// before the union sync has populated the committee/sponsor sections -- which
// is why it only asserts the static chrome.
const { test, expect } = require('@playwright/test')

test('home page renders the hero and section headings', async ({ page }) => {
  await page.goto('/')

  // The hero headline from HomeView.vue ("Build things that work.").
  await expect(page.getByRole('heading', { name: /Build things/ })).toBeVisible()

  // The two primary calls to action from the hero.
  await expect(page.getByRole('button', { name: 'Browse resources' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Book a locker' })).toBeVisible()

  // The upcoming-events section heading is rendered unconditionally, even
  // while the /events fetch is still in flight.
  await expect(page.getByRole('heading', { name: 'Upcoming events' })).toBeVisible()
})