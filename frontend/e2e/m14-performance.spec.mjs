import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { test, expect, fixtures, provider } from './helpers.mjs'

test('Multi-file upload keeps independent results and retries a failed file in place', async ({ page, request }) => {
  await provider(request)
  await page.goto('/')
  await page.getByLabel('Выберите документ', { exact: true }).setInputFiles([
    { name: 'batch-valid.txt', mimeType: 'text/plain', buffer: await readFile(path.join(fixtures, 'sample.txt')) },
    { name: 'batch-invalid.exe', mimeType: 'application/octet-stream', buffer: await readFile(path.join(fixtures, 'unsupported.exe')) },
  ])

  const valid = page.locator('.upload-queue-item').filter({ hasText: 'batch-valid.txt' })
  const invalid = page.locator('.upload-queue-item').filter({ hasText: 'batch-invalid.exe' })
  await expect(page.locator('.upload-queue-item')).toHaveCount(2)
  await expect(invalid).toContainText('Формат не поддерживается')
  await expect(valid).toContainText('Готово', { timeout: 150_000 })
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('document-checker-upload-queue') || '[]').length)).toBe(2)

  await page.reload()
  await page.getByRole('button', { name: /Загрузки/ }).click()
  const restoredInvalid = page.locator('.upload-queue-item').filter({ hasText: 'batch-invalid.exe' })
  await expect(restoredInvalid).toContainText('Формат не поддерживается')
  await restoredInvalid.getByRole('button', { name: 'Заменить файл batch-invalid.exe' }).click()
  await page.getByLabel('Выберите файл для повтора загрузки').setInputFiles(path.join(fixtures, 'sample.txt'))
  const retried = page.locator('.upload-queue-item').filter({ hasText: 'sample.txt' })
  await expect(page.locator('.upload-queue-item')).toHaveCount(2)
  await expect(retried).toContainText('Готово', { timeout: 150_000 })
  await expect(page.locator('.upload-queue-item').filter({ hasText: 'batch-valid.txt' })).toContainText('Готово')
})

test('Concurrent upload batches share the global two-file concurrency limit', async ({ page, request }) => {
  await provider(request)
  await page.goto('/')
  await page.setViewportSize({ width: 390, height: 844 })

  let activePosts = 0
  let peakPosts = 0
  let signalFirstPair
  const firstPairStarted = new Promise((resolve) => { signalFirstPair = resolve })
  await page.route('**/api/v1/documents', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    activePosts += 1
    peakPosts = Math.max(peakPosts, activePosts)
    if (activePosts >= 2) signalFirstPair()
    try {
      await new Promise((resolve) => setTimeout(resolve, 500))
      const response = await route.fetch()
      await route.fulfill({ response })
    } finally {
      activePosts -= 1
    }
  })

  const input = page.getByLabel('Выберите документ', { exact: true })
  const fixture = await readFile(path.join(fixtures, 'sample.txt'))
  await input.setInputFiles([
    { name: 'batch-a-one.txt', mimeType: 'text/plain', buffer: fixture },
    { name: 'batch-a-two.txt', mimeType: 'text/plain', buffer: fixture },
  ])
  await Promise.race([
    firstPairStarted,
    new Promise((_resolve, reject) => setTimeout(() => reject(new Error('The first two upload requests did not start')), 10_000)),
  ])
  await input.setInputFiles([
    { name: 'batch-b-one.txt', mimeType: 'text/plain', buffer: fixture },
    { name: 'batch-b-two.txt', mimeType: 'text/plain', buffer: fixture },
  ])

  await expect(page.locator('.upload-queue-item')).toHaveCount(4)
  for (const filename of ['batch-a-one.txt', 'batch-a-two.txt', 'batch-b-one.txt', 'batch-b-two.txt']) {
    await expect(page.locator('.upload-queue-item').filter({ hasText: filename })).toContainText('Готово', { timeout: 180_000 })
  }
  const queueBounds = await page.locator('.upload-queue-panel').boundingBox()
  expect(queueBounds).not.toBeNull()
  expect(queueBounds.x).toBeGreaterThanOrEqual(0)
  expect(queueBounds.x + queueBounds.width).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  expect(peakPosts).toBeLessThanOrEqual(2)
})

test('Large Markdown renders a bounded window and scrolls to the last source line', async ({ page, request }) => {
  await provider(request)
  await page.goto('/')
  const lines = Array.from({ length: 1_200 }, (_value, index) => `Line ${index + 1}: stable virtualized content.`)
  lines[1_199] = 'PERFORMANCE_LAST_LINE_SENTINEL'
  const uploaded = page.waitForResponse((response) => response.url().endsWith('/api/v1/documents') && response.request().method() === 'POST')
  await page.getByLabel('Выберите документ', { exact: true }).setInputFiles({
    name: 'virtualized.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from(lines.join('\n')),
  })
  const response = await uploaded
  expect(response.status(), await response.text()).toBe(202)
  const document = await response.json()
  await expect.poll(async () => (await (await request.get(`/api/v1/documents/${document.id}`)).json()).status, {
    timeout: 150_000,
    message: 'The large Markdown document completes parsing, indexing and analysis',
  }).toBe('ready')
  await page.getByRole('tab', { name: 'Markdown', exact: true }).click()

  const viewer = page.locator('.markdown-viewer')
  const renderedLineCount = await viewer.locator('.markdown-line').count()
  expect(renderedLineCount).toBeGreaterThan(0)
  expect(renderedLineCount).toBeLessThan(100)
  await viewer.evaluate((element) => element.scrollTo({ top: element.scrollHeight, behavior: 'auto' }))
  await expect(viewer.locator('.markdown-line-text', { hasText: 'PERFORMANCE_LAST_LINE_SENTINEL' })).toBeVisible()
  expect(await viewer.locator('.markdown-line').count()).toBeLessThan(100)
})
