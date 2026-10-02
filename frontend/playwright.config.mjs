import { defineConfig } from '@playwright/test'

const baseURL = process.env.PLAYWRIGHT_BASE_URL
if (!/^https?:\/\/(?:127\.0\.0\.1|localhost):5175$/.test(baseURL ?? '')) {
  throw new Error('Set PLAYWRIGHT_BASE_URL to the isolated E2E service on localhost:5175 before running browser tests.')
}

export default defineConfig({
  testDir: './e2e',
  testIgnore: '**/live-codex.spec.mjs',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  reporter: [['list'], ['html', { open: 'never' }], ['junit', { outputFile: 'test-results/results.xml' }]],
  use: {
    baseURL,
    browserName: 'chromium',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
})
