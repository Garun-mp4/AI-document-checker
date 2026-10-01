import { defineConfig } from '@playwright/test'

if (process.env.E2E_LIVE_CODEX_ACK !== 'send-synthetic-document-to-cloud') {
  throw new Error('Live Codex requires E2E_LIVE_CODEX_ACK=send-synthetic-document-to-cloud; only generated fixture text is sent.')
}
export default defineConfig({
  testDir: './e2e', testMatch: '**/live-codex.spec.mjs', workers: 1, retries: 0,
  timeout: 240_000, expect: { timeout: 30_000 },
  use: { baseURL: 'http://localhost:5175', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
})
