import { test, expect } from './helpers.mjs'

test('build manifest and diagnostic API match and browser assets use the intended cache policy', async ({ request }) => {
  const page = await request.get('/')
  expect(page.status()).toBe(200)
  expect(page.headers()['cache-control']).toContain('no-store')
  const html = await page.text()
  const scriptPath = html.match(/<script[^>]+src="([^"]+\.js)"/)?.[1]
  expect(scriptPath).toBeTruthy()

  const [manifestResponse, apiResponse] = await Promise.all([
    request.get('/build-info.json'),
    request.get('/api/v1/version'),
  ])
  expect(manifestResponse.status()).toBe(200)
  expect(apiResponse.status()).toBe(200)
  expect(manifestResponse.headers()['cache-control']).toContain('no-store')
  expect(apiResponse.headers()['cache-control']).toContain('no-store')
  const manifest = await manifestResponse.json()
  const api = await apiResponse.json()
  for (const key of ['build_id', 'commit', 'built_at']) {
    expect(manifest[key]).toBeTruthy()
    expect(api[key]).toBe(manifest[key])
  }

  const script = await request.get(scriptPath)
  expect(script.status()).toBe(200)
  expect(script.headers()['cache-control']).toContain('immutable')
  const workerPath = manifest.assets.find(asset => asset.includes('pdf.worker') && asset.endsWith('.mjs'))
  expect(workerPath).toBeTruthy()
  const worker = await request.get(`/${workerPath}`)
  expect(worker.status()).toBe(200)
  expect(worker.headers()['content-type']).toContain('application/javascript')
  expect(worker.headers()['cache-control']).toContain('immutable')
})

test('a stale API build is announced and can be refreshed', async ({ page }) => {
  await page.route('**/api/v1/version', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ service: 'api', version: '0.0.0', build_id: 'stale-build', commit: 'old-commit', built_at: '2026-01-01T00:00:00Z' }),
  }))
  await page.goto('/')
  const notice = page.getByTestId('build-version-mismatch')
  await expect(notice).toBeVisible()
  await expect(notice).toContainText('разных версиях')
  await expect(notice.getByRole('button', { name: 'Обновить' })).toBeEnabled()
})

test('processing progress restores after reload and offers cancel and retry', async ({ page, request }) => {
  test.setTimeout(180_000)
  await request.post('/api/v1/__e2e/provider', { data: {} })
  await request.post('/api/v1/__e2e/provider', { data: { hold_stage: 'extracting' } })
  await page.goto('/')
  const uploadResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/documents') && response.request().method() === 'POST')
  await page.getByLabel('Выберите документ', { exact: true }).setInputFiles('e2e/fixtures/sample.txt')
  const uploaded = await uploadResponse
  expect(uploaded.status()).toBe(202)
  const { id } = await uploaded.json()

  await expect.poll(async () => (await (await request.get(`/api/v1/documents/${id}/jobs`)).json())[0]?.stage).toBe('extracting')
  const progress = page.getByTestId('processing-status')
  await expect(progress).toContainText('Читаю документ')
  await expect(progress.getByRole('button', { name: 'Отменить' })).toBeVisible()
  await page.reload()
  await expect(page.getByTestId('processing-status')).toContainText('Читаю документ')

  await page.getByTestId('processing-status').getByRole('button', { name: 'Отменить' }).click()
  await expect.poll(async () => (await (await request.get(`/api/v1/documents/${id}`)).json()).status).toBe('cancelled')
  await expect(page.locator('.issue-banner')).toContainText('Обработка отменена')

  await request.post('/api/v1/__e2e/provider', { data: {} })
  await page.locator('.issue-banner').getByRole('button', { name: 'Повторить' }).click()
  await expect.poll(async () => (await (await request.get(`/api/v1/documents/${id}`)).json()).status,
    { timeout: 150_000 }).toBe('ready')
  await expect(page.getByTestId('processing-status')).toHaveCount(0)
  await request.delete(`/api/v1/documents/${id}`)
})
