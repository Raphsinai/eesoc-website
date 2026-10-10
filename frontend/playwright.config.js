// Playwright config for the e2e suites (tests/e2e/). Plain ESM — the frontend
// package.json declares "type": "module".
//
// The specs run against the compose TEST stack (see tests/README.md), not a
// dev server: they need the union stub and mailpit, and must never touch the
// real ICU bridge. baseURL is the frontend, so specs use the same-origin /api
// the browser uses.
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';

// Where tests/e2e/auth.setup.js saves the admin's session. Absolute, because
// Playwright resolves a relative storageState against the working directory —
// the repo root, where the suites are run from — not against this file, so
// '../tests/e2e/.auth/admin.json' pointed outside the repository and every
// spec in the `admin` project failed with ENOENT.
const ADMIN_STATE = fileURLToPath(new URL('../tests/e2e/.auth/admin.json', import.meta.url));

export default defineConfig({
  testDir: '../tests/e2e',

  // One worker in CI. The spa-assets specs rebuild and recreate the shared
  // frontend container mid-test, and say they are safe because nothing runs
  // beside them — but Playwright does not default to one worker under CI, so
  // with the runner's two CPUs both of those specs rebuilt the same container
  // at once and each failed on the other's build. Locally, pass --workers=1
  // (or CI=1) when running them.
  workers: process.env.CI ? 1 : undefined,

  // Every .spec.js in the tree runs twice — once signed out, once as the
  // admin the setup project signs in — because sections keep adding specs and
  // neither project's match pattern should need editing when they do. A spec
  // that must start signed out opts back out per-test:
  //   test.use({ storageState: { cookies: [], origins: [] } })
  projects: [
    {
      // Signs test-admin@ic.ac.uk in via dev-login (the test stack only —
      // the api refuses that endpoint unless ENVIRONMENT=development) and
      // saves the session cookies to .auth/admin.json.
      name: 'setup',
      testMatch: /auth\.setup\.js/,
    },
    {
      name: 'public',
      testMatch: /\.spec\.js$/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'admin',
      testMatch: /\.spec\.js$/,
      dependencies: ['setup'],
      use: {
        ...devices['Desktop Chrome'],
        storageState: ADMIN_STATE,
      },
    },
  ],

  // Traces only around retries, and cleaned up when a test passes, so the
  // artifact upload in CI (actions/upload-artifact on failure) stays small
  // while a failure still shows what happened.
  //
  // baseURL lives here rather than at the config root: root-level `use` is
  // merged into every project's `use`, so all three projects inherit it and a
  // spec's `page.goto('/')` and `request.post('/api/...')` resolve against
  // the compose frontend on :8080.
  use: {
    baseURL: 'http://localhost:8080',
    trace: 'retain-on-failure',
  },

  reporter: [
    ['list'],
    // Written for CI to upload; `open: never` stops it launching a browser
    // server at the end of a local run.
    ['html', { open: 'never' }],
  ],
});
