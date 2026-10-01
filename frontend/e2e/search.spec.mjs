import { test, expect, provider, upload } from './helpers.mjs'

async function uploadText(page, name, contents) {
  await page.goto('/')
  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/v1/documents') && response.request().method() === 'POST')
  await page.getByLabel('Выберите документ', { exact: true }).setInputFiles({
    name,
    mimeType: 'text/plain',
    buffer: Buffer.from(contents, 'utf8'),
  })
  const response = await uploaded
  expect(response.status(), await response.text()).toBe(202)
  const document = await response.json()
  await expect.poll(async () => {
    const current = await page.request.get(`/api/v1/documents/${document.id}`)
    return (await current.json()).status
  }, { timeout: 300_000 }).toBe('ready')
  await expect(page.locator('.insight-card')).toHaveCount(7)
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
  await expect(page.getByRole('search', { name: 'Поиск в документе' })).toBeVisible()
  return document
}

test('Search scans the complete original and Markdown, paginates results, and highlights exact locations', async ({ page, request }) => {
  test.setTimeout(360_000)
  await provider(request)

  const lines = Array.from({ length: 210 }, (_, index) =>
    `Абзац ${index + 1}: ${'содержимое документа без совпадения '.repeat(14)} HIT_TOKEN`,
  )
  lines.push(`Последний абзац: ${'продолжение текста для проверки полной области поиска '.repeat(160)} END_TOKEN`)
  const document = await uploadText(page, 'search-long.txt', lines.join('\n'))
  const providerCallsBefore = (await (await request.get('/api/v1/__e2e/worker-control')).json()).complete_calls

  const search = page.getByRole('search', { name: 'Поиск в документе' })
  const input = search.getByRole('searchbox', { name: 'Найти в документе' })
  const status = search.locator('.document-search-status')
  const next = search.getByRole('button', { name: 'Следующее совпадение' })

  const lateOriginal = await request.get(`/api/v1/documents/${document.id}/search?q=HIT_TOKEN&scope=original&offset=200&limit=10`)
  expect(lateOriginal.ok()).toBeTruthy()
  const originalPage = await lateOriginal.json()
  expect(originalPage.total).toBe(210)
  expect(originalPage.matches).toHaveLength(10)
  expect(originalPage.matches[0].locator.char_start).toBeGreaterThan(100_000)
  expect(originalPage.matches[0].locator.source_type).toBe('text_range')

  await input.fill('HIT_TOKEN')
  await expect(status).toHaveText('1 из 210')
  await expect(page.locator('.source-text-line mark.document-search-highlight')).toContainText('HIT_TOKEN')
  await input.press('Enter')
  await expect(status).toHaveText('2 из 210')
  await input.press('Shift+Enter')
  await expect(status).toHaveText('1 из 210')
  for (let index = 0; index < 50; index += 1) await next.click()
  await expect(status).toHaveText('51 из 210')
  await expect(page.locator('.source-text-line mark.document-search-highlight')).toContainText('HIT_TOKEN')

  const markdownHit = await request.get(`/api/v1/documents/${document.id}/search?q=END_TOKEN&scope=markdown`)
  expect(markdownHit.ok()).toBeTruthy()
  const markdownResult = await markdownHit.json()
  expect(markdownResult.total).toBe(1)
  expect(markdownResult.matches[0].markdown_start).toBeGreaterThan(100_000)

  await input.fill('END_TOKEN')
  await expect(status).toHaveText('1 из 1')
  await expect(page.locator('.source-text-line mark.document-search-highlight')).toContainText('END_TOKEN')
  await search.locator('.document-search-scopes').getByRole('button', { name: 'Markdown', exact: true }).click()
  await expect(page.locator('.markdown-search-highlight')).toContainText('END_TOKEN')
  await expect(page.locator('.markdown-viewer')).toContainText('Последний абзац')

  await input.fill('NEVER_PRESENT')
  await expect(status).toHaveText('Совпадений нет')
  await expect(page.locator('.markdown-search-highlight')).toHaveCount(0)
  await input.press('Escape')
  await expect(input).toHaveValue('')
  await expect(status).toHaveText('Введите слово или фразу')

  const providerCallsAfter = (await (await request.get('/api/v1/__e2e/worker-control')).json()).complete_calls
  expect(providerCallsAfter, 'Manual search must not call the Codex provider').toBe(providerCallsBefore)
  const messages = await (await request.get(`/api/v1/chats/${(await (await request.get('/api/v1/chats')).json()).find(item => item.document_id === document.id).id}/messages`)).json()
  expect(messages, 'Manual search must not add chat messages').toHaveLength(0)
})

test('Search ignores stale responses, clears errors, and resets when another document is selected', async ({ page, request }) => {
  test.setTimeout(240_000)
  await provider(request)
  const document = await uploadText(page, 'search-race.txt', 'First line END_TOKEN\nSecond line ordinary text')
  const search = page.getByRole('search', { name: 'Поиск в документе' })
  const input = search.getByRole('searchbox', { name: 'Найти в документе' })
  const status = search.locator('.document-search-status')

  await page.route(`**/api/v1/documents/${document.id}/search?q=SLOW_QUERY*`, async (route) => {
    await new Promise(resolve => setTimeout(resolve, 700))
    try { await route.continue() } catch { /* The input's AbortController may already have cancelled it. */ }
  })
  const staleRequest = page.waitForRequest(url => url.url().includes('q=SLOW_QUERY'))
  await input.fill('SLOW_QUERY')
  await staleRequest
  await input.fill('END_TOKEN')
  await expect(status).toHaveText('1 из 1')
  await expect(page.locator('.source-text-line mark.document-search-highlight')).toContainText('END_TOKEN')
  await page.waitForTimeout(800)
  await expect(status).toHaveText('1 из 1')

  await page.unrouteAll({ behavior: 'wait' })
  await page.route(`**/api/v1/documents/${document.id}/search?q=SERVER_ERROR*`, route => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Поиск временно недоступен.' }),
  }))
  await input.fill('SERVER_ERROR')
  await expect(status).toHaveText('Поиск временно недоступен.')
  await expect(page.locator('.source-text-line mark.document-search-highlight')).toHaveCount(0)
  await page.unrouteAll({ behavior: 'wait' })

  const another = await uploadText(page, 'search-next.txt', 'Другой документ содержит OTHER_TOKEN')
  expect(another.id).not.toBe(document.id)
  await expect(page.getByRole('searchbox', { name: 'Найти в документе' })).toHaveValue('')
  await expect(page.locator('.source-text-line mark.document-search-highlight')).toHaveCount(0)
})

test('Opening a citation clears the active query without clearing the selected source', async ({ page, request }) => {
  await provider(request)
  const document = await uploadText(page, 'search-citation.txt', 'A source paragraph contains CITATION_TOKEN and should remain selected.')
  const search = page.getByRole('search', { name: 'Поиск в документе' })
  const input = search.getByRole('searchbox', { name: 'Найти в документе' })
  await input.fill('CITATION_TOKEN')
  await expect(search.locator('.document-search-status')).toHaveText('1 из 1')
  await expect(page.locator('.document-search-highlight')).toBeVisible()

  const citation = page.locator('.citation-chip').first()
  await expect(citation).toBeVisible()
  await citation.click()

  await expect(input).toHaveValue('')
  await expect(page.locator('.preview-source-callout')).toContainText('Выбран источник')
  await expect(page.locator('.original-viewer-body')).toHaveAttribute('data-selected-source', /.+/)
  await expect(page.locator('.source-text-line mark')).toContainText('A source paragraph contains CITATION_TOKEN')
})

test('Search follows OCR boxes, spreadsheet cells, and structured slide/chapter sources', async ({ page, request }) => {
  test.setTimeout(480_000)
  await provider(request)

  const pdf = await upload(page, 'scan.pdf')
  const ocrSearch = await request.get(`/api/v1/documents/${pdf.id}/search?q=${encodeURIComponent('Алексей')}&scope=original`)
  expect(ocrSearch.ok()).toBeTruthy()
  const ocrResult = await ocrSearch.json()
  expect(ocrResult.total).toBeGreaterThan(0)
  expect(ocrResult.matches[0].locator.ocr).toBe(true)
  expect(ocrResult.matches[0].locator.ocr_map.word_boxes.length).toBeGreaterThan(0)
  let toolbar = page.getByRole('search', { name: 'Поиск в документе' })
  await toolbar.getByRole('searchbox', { name: 'Найти в документе' }).fill('Алексей')
  await expect(toolbar.locator('.document-search-status')).toContainText('из ')
  await expect(page.locator('.pdf-ocr-highlight-box').first()).toBeVisible()

  for (const item of [
    { name: 'sample.csv', query: 'Гамма', locate: { row_start: 4, column: 'Проект' }, selector: '.source-cell-match' },
    { name: 'multisheet.xlsx', query: 'Сигма', locate: { row_start: 2, sheet: 'Второй лист', column: 'Проект' }, selector: '.source-cell-match' },
    { name: 'sample.pptx', query: 'обработку презентации', locate: { slide: 1 }, selector: '.source-map-original-viewer .source-match mark' },
    { name: 'sample.epub', query: 'ссылки на главу', locate: { chapter: 1 }, selector: '.source-map-original-viewer .source-match mark' },
  ]) {
    const document = await upload(page, item.name)
    const response = await request.get(`/api/v1/documents/${document.id}/search?q=${encodeURIComponent(item.query)}&scope=original`)
    expect(response.ok(), `${item.name} search should succeed`).toBeTruthy()
    const result = await response.json()
    expect(result.total, `${item.name} result count`).toBeGreaterThan(0)
    expect(result.matches[0].locator).toMatchObject(item.locate)

    toolbar = page.getByRole('search', { name: 'Поиск в документе' })
    await toolbar.getByRole('searchbox', { name: 'Найти в документе' }).fill(item.query)
    await expect(toolbar.locator('.document-search-status')).toContainText('из ')
    await expect(page.locator(item.selector).first()).toBeVisible()
    if (item.name === 'multisheet.xlsx') {
      await expect(page.getByRole('combobox', { name: 'Лист исходного файла' })).toHaveValue('Второй лист')
    }
  }
})
