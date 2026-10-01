import { test, expect, upload, provider, originalVisible, showChat } from './helpers.mjs'

test.beforeEach(async ({ request }) => provider(request))

const formats = {
  pdf: 'pdf', docx: 'docx', txt: 'text', md: 'text', csv: 'csv', xml: 'text',
  xlsx: 'csv', xls: 'csv', pptx: 'mapped', html: 'text', htm: 'text', json: 'text', epub: 'mapped',
}

for (const [extension, renderer] of Object.entries(formats)) {
  test(`${extension}: upload → original → Markdown → citation → download`, async ({ page }) => {
    const doc = await upload(page, `sample.${extension}`)
    await originalVisible(page, renderer)
    const state = await (await page.request.get(`/api/v1/documents/${doc.id}`)).json()
    expect(state.chunk_count).toBeGreaterThan(0)
    expect(state.markdown_status).toBe('ready')
    const preview = await (await page.request.get(`/api/v1/documents/${doc.id}/preview`)).json()
    expect(preview.blocks[0].locator).toMatchObject({
      locator_version: 1,
      document_id: doc.id,
      processing_version: state.active_version,
    })
    expect(preview.blocks[0].locator.source_range.coordinate_space).toBeTruthy()
    const citation = page.locator('.citation-chip').first()
    await expect(citation).toBeVisible()
    await citation.click()
    await expect(page.locator('.preview-source-callout')).toContainText('Выбран источник')
    await expect(page.locator('.original-viewer-body')).toHaveAttribute('data-selected-source', /.+/)
    const highlights = { pdf: '.pdf-text-match', docx: '.source-match', text: '.source-line-range, mark', csv: '.source-row-match', mapped: '.source-match' }
    await expect(page.locator(`#document-original-viewer ${highlights[renderer]}`).first()).toBeVisible()
    if (extension === 'xml') {
      await expect(page.locator('.original-text-viewer mark')).toHaveCount(4)
      const chunks = await (await page.request.get(`/api/v1/documents/${doc.id}/chunks?limit=200`)).json()
      expect(chunks.map(chunk => chunk.text).join('\n')).toContain('Алексей Пример')
    }
    if (['xlsx', 'xls'].includes(extension)) await expect(page.locator('.source-row-match')).toHaveCount(2)
    await page.getByRole('tab', { name: 'Markdown', exact: true }).click()
    await expect(page.locator('.markdown-viewer')).toBeVisible()
    if (state.markdown_status === 'ready') {
      await expect(page.locator('.markdown-line-active').first()).toBeVisible()
      const downloadPromise = page.waitForEvent('download')
      await page.getByRole('link', { name: 'Скачать .md', exact: true }).click()
      const download = await downloadPromise
      expect(download.suggestedFilename()).toMatch(/\.md$/)
      expect(await download.failure()).toBeNull()
    }
    await citation.click()
    await expect(page.getByRole('tab', { name: 'Оригинал', exact: true })).toHaveAttribute('aria-selected', 'true')
    await page.reload()
    await expect(page.locator('.insight-card')).toHaveCount(7)
    await originalVisible(page, renderer)
  })
}

for (const [name, renderer] of [
  ['cp1251.txt', 'text'], ['cp1251.md', 'text'], ['cp1251.csv', 'csv'],
  ['quoted.csv', 'csv'], ['tab.csv', 'csv'], ['long.txt', 'text'],
  ['multipage.pdf', 'pdf'], ['multipage.docx', 'docx'], ['multisheet.xlsx', 'csv'],
  ['multisheet.xls', 'csv'], ['multislide.pptx', 'mapped'], ['multichapter.epub', 'mapped'],
]) {
  test(`Structure and encoding: ${name}`, async ({ page }) => {
    const doc = await upload(page, name)
    await originalVisible(page, renderer)
    if (name.startsWith('cp1251')) await expect(page.locator('#document-original-viewer')).toContainText('Алексей|Альфа'.split('|')[renderer === 'csv' ? 1 : 0])
    if (name === 'multipage.pdf') {
      await expect(page.locator('.preview-page-label')).toHaveText('1 / 3')
      await page.getByRole('button', { name: 'Следующая страница' }).click()
      await expect(page.locator('.preview-page-label')).toHaveText('2 / 3')
      await originalVisible(page, 'pdf')
    }
    if (name === 'multipage.docx') await expect(page.locator('.docx-render-host')).toContainText('Вторая страница')
    if (name === 'multislide.pptx') await expect(page.locator('.source-map-original-viewer')).toContainText('Второй слайд')
    if (name === 'multichapter.epub') await expect(page.locator('.source-map-original-viewer')).toContainText('Вторая глава')
    if (name.startsWith('multisheet')) {
      const preview = await (await page.request.get(`/api/v1/documents/${doc.id}/preview`)).json()
      expect(preview.blocks.some(block => block.locator.sheet)).toBeTruthy()
    }
    if (name === 'quoted.csv') await expect(page.locator('.original-csv-table')).toContainText('запятая, кавычки "да"')
  })
}

test('CSV aggregates and actual row pagination', async ({ page }) => {
  await upload(page, 'sample.csv')
  const metrics = page.locator('.insight-card').filter({ hasText: 'Числовые показатели' })
  await expect(metrics).toContainText('60')
  await expect(metrics).toContainText('300')
  await upload(page, 'large.csv')
  await expect(page.locator('.original-csv-table tbody tr')).toHaveCount(100)
  await page.getByRole('button', { name: 'Показать ещё', exact: true }).click()
  await expect(page.locator('.original-csv-table tbody tr')).toHaveCount(200)
  await page.getByRole('button', { name: 'Показать ещё', exact: true }).click()
  await expect(page.locator('.original-csv-table tbody tr')).toHaveCount(240)
  await expect(page.getByRole('button', { name: 'Показать ещё', exact: true })).toHaveCount(0)
})

test('Citation loads and highlights a matching CSV range beyond the first table page', async ({ page }) => {
  const doc = await upload(page, 'large.csv')
  const seeded = await page.request.post('/api/v1/__e2e/citation', { data: { document_id: doc.id, row: 238 } })
  expect(seeded.ok()).toBeTruthy()
  await page.reload()
  await showChat(page)
  const citation = page.locator('.assistant-message .inline-citation').last()
  await expect(citation).toBeVisible()
  await citation.click()

  await expect(page.locator('.preview-source-callout')).toContainText('Проект 237')
  const matchingRow = page.locator('.original-csv-table tbody tr.source-row-match').filter({ hasText: 'Проект 237' })
  await expect(matchingRow).toBeVisible()
  const rowNumber = Number(await matchingRow.locator('.csv-row-number').textContent())
  expect(rowNumber).toBeGreaterThan(101)
  await expect(page.locator('.preview-source-callout')).toContainText('Найден и подсвечен')
})

for (const extension of ['xlsx', 'xls']) {
  test(`${extension}: citation switches to its sheet and row`, async ({ page }) => {
    const doc = await upload(page, `multisheet.${extension}`)
    const seeded = await page.request.post('/api/v1/__e2e/citation', {
      data: { document_id: doc.id, row: 2, sheet: extension === 'xlsx' ? 'Второй лист' : 'Второй' },
    })
    expect(seeded.ok()).toBeTruthy()
    await page.reload()
    await showChat(page)
    const citation = page.locator('.assistant-message .inline-citation').last()
    await expect(citation).toBeVisible()
    await citation.click()

    const sheet = page.getByRole('combobox', { name: 'Лист исходного файла' })
    await expect(sheet).toHaveValue(extension === 'xlsx' ? 'Второй лист' : 'Второй')
    await expect(page.locator('.original-csv-table tbody tr[data-row-number="2"]')).toContainText('Сигма')
    await expect(page.locator('.preview-source-callout')).toContainText('Найден и подсвечен')
  })
}

test('Mobile citation opens the viewer and keeps the source range in view', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await upload(page, 'sample.txt')
  await page.locator('.citation-chip').first().click()
  const viewer = page.locator('#document-original-viewer')
  await expect(viewer.locator('.original-text-viewer mark').first()).toBeVisible()
  const measure = () => page.evaluate(() => {
    const viewer = document.querySelector('#document-original-viewer')
    const mark = viewer?.querySelector('.original-text-viewer mark')
    if (!viewer || !mark) return null
    const outer = viewer.getBoundingClientRect()
    const target = mark.getBoundingClientRect()
    return { outerVisible: outer.bottom > 0 && outer.top < innerHeight, targetVisible: target.bottom > 0 && target.top < innerHeight, documentWidth: document.documentElement.scrollWidth }
  })
  await expect.poll(measure, { timeout: 5_000 }).toMatchObject({ outerVisible: true, targetVisible: true })
  const geometry = await measure()
  expect(geometry?.outerVisible).toBeTruthy()
  expect(geometry?.targetVisible).toBeTruthy()
  expect(geometry?.documentWidth).toBeLessThanOrEqual(390)
})

test('Real local Russian OCR → page canvas → source citation', async ({ page }) => {
  const doc = await upload(page, 'scan.pdf')
  const state = await (await page.request.get(`/api/v1/documents/${doc.id}`)).json()
  expect(state.ocr_status).toBe('ready')
  expect(state.analysis_source).toBe('ocr')
  await originalVisible(page, 'pdf')
  await expect(page.locator('.ocr-notice')).toContainText('распознан локально')
  await page.locator('.citation-chip').first().click()
  await expect(page.locator('.preview-source-callout')).toContainText('Выбран источник')
  await page.getByRole('tab', { name: 'Markdown', exact: true }).click()
  await expect(page.locator('.markdown-viewer')).toContainText('Алексей')
})

test('Mixed PDF keeps native pages, OCRs scanned pages, and highlights OCR word coordinates', async ({ page }) => {
  const doc = await upload(page, 'mixed.pdf')
  const state = await (await page.request.get(`/api/v1/documents/${doc.id}`)).json()
  expect(state.ocr_status).toBe('ready')
  expect(state.analysis_source).toBe('ocr')

  const chunks = await (await page.request.get(`/api/v1/documents/${doc.id}/chunks?limit=200`)).json()
  const ocrSources = chunks.filter(chunk => chunk.locator.ocr === true)
  expect(new Set(ocrSources.map(chunk => chunk.locator.page))).toEqual(new Set([2, 4]))
  for (const source of ocrSources) {
    const map = source.locator.ocr_map
    expect(map.coordinate_space).toBe('page-normalized-top-left')
    expect(map.rotation).toBe(source.locator.page === 4 ? 90 : 0)
    expect(map.word_boxes.length).toBeGreaterThan(0)
    expect(map.line_boxes.length).toBeGreaterThan(0)
    for (const [x0, y0, x1, y1, start, end] of map.word_boxes) {
      expect(x0).toBeGreaterThanOrEqual(0)
      expect(y0).toBeGreaterThanOrEqual(0)
      expect(x1).toBeLessThanOrEqual(map.coordinate_scale)
      expect(y1).toBeLessThanOrEqual(map.coordinate_scale)
      expect(x1).toBeGreaterThan(x0)
      expect(y1).toBeGreaterThan(y0)
      expect(end).toBeGreaterThan(start)
    }
  }
  expect(chunks.some(chunk => chunk.locator.page === 1 && chunk.locator.ocr !== true)).toBeTruthy()
  expect(chunks.some(chunk => chunk.locator.page === 3)).toBeFalsy()

  const preview = await (await page.request.get(`/api/v1/documents/${doc.id}/preview`)).json()
  expect(preview.page_count).toBe(4)
  await originalVisible(page, 'pdf')
  const pageTwoOcrSource = ocrSources.find(source => source.locator.page === 2)
  const citation = page.locator(`.citation-chip[data-source-id="${pageTwoOcrSource.id}"]`).first()
  await expect(citation).toBeVisible()
  await citation.click()
  await expect(page.locator('.preview-source-callout')).toContainText('Выбран источник')
  await expect(page.locator('.pdf-ocr-highlight-box').first()).toBeVisible()
  const geometry = await page.locator('.pdf-ocr-highlight-box').first().evaluate(element => {
    const box = element.getBoundingClientRect()
    const sheet = element.closest('.pdf-page-sheet').getBoundingClientRect()
    return { left: box.left - sheet.left, top: box.top - sheet.top, right: box.right - sheet.left, bottom: box.bottom - sheet.top, width: sheet.width, height: sheet.height }
  })
  expect(geometry.left).toBeGreaterThanOrEqual(0)
  expect(geometry.top).toBeGreaterThanOrEqual(0)
  expect(geometry.right).toBeLessThanOrEqual(geometry.width + 1)
  expect(geometry.bottom).toBeLessThanOrEqual(geometry.height + 1)
})
