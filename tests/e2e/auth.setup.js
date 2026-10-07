// Signs an admin in through dev-login and saves the session cookies as the
// storage state the `admin` project runs every spec with.
//
// CommonJS on purpose: this file lives in tests/e2e, outside the frontend
// package that declares "type": "module", so an ESM syntax here fails to load
// with "Cannot use 'import.meta' outside a module". The specs use the same
// convention — everything under tests/e2e is require().
//
// dev-login only exists on the TEST stack's api (the endpoint 404s unless
// ENVIRONMENT=development and DEV_LOGIN_ENABLED=true), so nothing here can
// ever mint a session against a deployed site.
const { test: setup, expect } = require('@playwright/test')
const { join } = require('node:path')

// Beside the specs, not under frontend/: it is a test artifact, gitignored
// (.auth/ holds a live session — never commit it).
const STATE_PATH = join(__dirname, '.auth', 'admin.json')

setup('sign the admin in via dev-login', async ({ request }) => {
  const response = await request.post('/api/auth/dev-login', {
    data: { email: 'test-admin@ic.ac.uk', make_admin: true },
  })
  // Assert before saving: a stored 401 page as "admin" would make every admin
  // spec fail confusingly far from the actual breakage.
  expect(response.status()).toBe(200)

  // The request fixture carries the session + csrf cookies the api just set;
  // saving its storage state is what the `admin` project's specs inherit.
  await request.storageState({ path: STATE_PATH })
})
