// Browser acceptance tests for an isolated frontend built from production's
// current source + ONLY the files approved for one selective release.
// The workflow specifies the exact test file in its command invocation.
const { defineConfig, devices } = require('@playwright/test')

module.exports = defineConfig({
  testDir: '.',
  workers: 1,
  projects: [{ name: 'public', use: { ...devices['Desktop Chrome'] } }],
  use: {
    baseURL: 'http://127.0.0.1:4174',
    trace: 'retain-on-failure',
  },
  reporter: [['list']],
})
