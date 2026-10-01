import path from 'node:path'
import { test, expect, fixtures } from './helpers.mjs'

test('Explicit live Codex: synthetic TXT upload, seven answers, sourced chat', async ({ page, request }) => {
  expect((await request.post('/api/v1/__e2e/provider', { data: {} })).status()).toBe(404)
  const status = await (await request.get('/api/v1/codex/status')).json()
  expect(status.authenticated, 'Sign in to Codex in the isolated live UI on :5175 first').toBe(true)
  await page.goto('/')
  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/v1/documents') && response.request().method() === 'POST')
  await page.getByLabel('Выберите документ', { exact: true }).setInputFiles(path.join(fixtures, 'sample.txt'))
  const doc = await (await uploaded).json()
  try {
    await expect.poll(async () => (await (await request.get(`/api/v1/documents/${doc.id}`)).json()).status, { timeout: 180_000 }).toBe('ready')
    await expect(page.locator('.insight-card')).toHaveCount(7)
    await page.getByLabel('Сообщение для чата', { exact: true }).fill('Кто автор документа?')
    await page.getByRole('button', { name: 'Отправить вопрос', exact: true }).click()
    await expect(page.locator('.assistant-message')).toContainText('Алексей')
    await expect(page.locator('.assistant-message .message-sources button').first()).toBeVisible()
  } finally {
    expect((await request.delete(`/api/v1/documents/${doc.id}`)).ok()).toBeTruthy()
  }
})
