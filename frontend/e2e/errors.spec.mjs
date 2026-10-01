import path from 'node:path'
import { readFile } from 'node:fs/promises'
import { test, expect, upload, provider, fixtures, originalVisible } from './helpers.mjs'

test.beforeEach(async ({ request }) => provider(request))

for (const [name, status] of [['empty.txt', 400], ['unsupported.exe', 415], ['oversized.txt', 413],
  ['corrupt.docx', 422], ['wrong.pdf', 422], ['dangerous.xml', 422]]) {
  test(`Rejected upload: ${name}`, async ({ page }) => {
    await page.goto('/')
    const response = !['unsupported.exe', 'oversized.txt'].includes(name) ? page.waitForResponse(r => r.url().endsWith('/api/v1/documents') && r.request().method() === 'POST') : null
    await page.getByLabel('Выберите документ', { exact: true }).setInputFiles(path.join(fixtures, name))
    if (response) expect((await response).status()).toBe(status)
    await expect(page.locator('.toast-message')).toBeVisible()
    const server = await page.request.post('/api/v1/documents', { multipart: { file: { name, mimeType: 'application/octet-stream', buffer: await readFile(path.join(fixtures, name)) } } })
    expect(server.status()).toBe(status)
    await expect(page.getByRole('button', { name: 'Новый чат', exact: true })).toBeEnabled()
  })
}

for (const name of ['corrupt.pdf', 'encrypted.pdf', 'unreadable.pdf']) {
  test(`Processing failure is recoverable: ${name}`, async ({ page }) => {
    const doc = await upload(page, name, 'error')
    const state = await (await page.request.get(`/api/v1/documents/${doc.id}`)).json()
    expect(state.error_message).toBeTruthy()
    await page.getByRole('button', { name: 'Новый чат', exact: true }).click()
    await expect(page.getByRole('heading', { name: /Загрузите документ/ })).toBeVisible()
    await expect(page.locator('.library .document-select').first()).toBeVisible()
  })
}

test('HTML is source text: no script, handler or external resource runs', async ({ page }) => {
  const external = []
  page.on('request', request => { if (request.url().includes('example.invalid')) external.push(request.url()) })
  await upload(page, 'unsafe.html')
  await originalVisible(page, 'text')
  await expect(page.locator('.original-text-viewer')).toContainText('<script>')
  expect(await page.evaluate(() => window.__unsafeExecuted)).toBeUndefined()
  expect(external).toEqual([])
  const doc = await (await page.request.get('/api/v1/documents')).json()
  const current = doc.find(item => item.filename === 'unsafe.html')
  const original = await page.request.get(`/api/v1/documents/${current.id}/file`)
  expect(original.headers()['content-type']).toContain('text/plain')
  expect(original.headers()['x-content-type-options']).toBe('nosniff')
  expect(original.headers()['content-security-policy']).toContain("default-src 'none'")
})

for (const extension of ['pdf', 'docx']) {
  test(`${extension} render failure: fallback, seven answers and retry survive`, async ({ page }) => {
    // Fault is confined to this browser's original-file fetch, not the stored file.
    await page.route('**/api/v1/documents/*/file', route => route.fulfill({ status: 200, body: 'invalid renderer input', contentType: 'application/octet-stream' }))
    const doc = await upload(page, `sample.${extension}`)
    if (extension === 'pdf') {
      // PDF.js degrades to the browser's native viewer before the text fallback.
      await expect(page.locator('.pdf-native-fallback iframe')).toBeVisible()
      await expect(page.locator('.pdf-native-fallback-note')).toBeVisible()
    } else {
      await expect(page.locator('.preview-render-error')).toBeVisible()
      await expect(page.locator('.preview-fallback')).toContainText('Текстовый контекст')
      await expect(page.getByRole('link', { name: 'Открыть файл', exact: true })).toBeVisible()
    }
    expect((await (await page.request.get(`/api/v1/documents/${doc.id}`)).json()).status).toBe('ready')
    await expect(page.locator('.insight-card')).toHaveCount(7)
    await page.unroute('**/api/v1/documents/*/file')
    await page.reload()
    await originalVisible(page, extension)
  })
}

test('Codex unavailable and disconnected states do not discard original', async ({ page, request }) => {
  const doc = await upload(page, 'sample.txt')
  for (const mode of ['disconnected', 'unavailable']) {
    await provider(request, { mode })
    await page.reload()
    await expect(page.getByLabel('Сообщение для чата', { exact: true })).toBeDisabled()
    await originalVisible(page, 'text')
    expect((await (await request.get(`/api/v1/documents/${doc.id}`)).json()).status).toBe('ready')
  }
  await provider(request)
})
