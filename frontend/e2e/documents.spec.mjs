import { test, expect, upload, provider, originalVisible, showChat, fixtures } from './helpers.mjs'
import { readFile } from 'node:fs/promises'
import path from 'node:path'

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

test('Table tools query every row and save both reproducible calculation scopes', async ({ page }) => {
  const doc = await upload(page, 'large.csv')
  const table = page.locator('.original-csv-table')

  await page.getByRole('button', { name: 'Сортировать по столбцу Часы по возрастанию' }).click()
  await page.getByRole('button', { name: 'Сортировать по столбцу Часы по убыванию' }).click()
  await expect(table.locator('tbody tr').first()).toHaveAttribute('data-row-number', '241')
  await expect(table.locator('tbody tr').first()).toContainText('Проект 240')

  await page.getByLabel('Столбец фильтра').selectOption('0')
  await page.getByLabel('Тип фильтра').selectOption('text')
  await page.getByLabel('Условие фильтра').selectOption('contains')
  await page.getByLabel('Значение фильтра').fill('Проект 237')
  await page.getByRole('button', { name: 'Применить', exact: true }).click()
  await expect(table.locator('tbody tr')).toHaveCount(1)
  await expect(table.locator('tbody tr').first()).toHaveAttribute('data-row-number', '238')
  await expect(table.locator('tbody tr').first()).toContainText('Проект 237')
  await expect(page.locator('.csv-table-footer')).toContainText('1 из 1 строк')

  await page.locator('.csv-calculation-panel > summary').click()
  await expect(page.getByLabel('Столбец для расчёта')).toHaveValue('1')
  const responsePromise = page.waitForResponse(response => response.url().includes(`/documents/${doc.id}/preview/table/calculations`) && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Рассчитать', exact: true }).click()
  const response = await responsePromise
  expect(response.ok()).toBeTruthy()
  const result = await response.json()
  expect(result.document.sum).toBe('28920')
  expect(result.document.average).toBe('120.50')
  expect(result.filtered.sum).toBe('237')
  expect(result.filtered_source.locator.calculation_scope).toBe('current_filter')
  expect(result.filtered_source.locator.filter.value).toBe('Проект 237')
  await expect(page.locator('.csv-calculation-scope').first()).toContainText('28 920')
  await expect(page.locator('.csv-calculation-scope').last()).toContainText('237')

  const chunks = await (await page.request.get(`/api/v1/documents/${doc.id}/chunks?offset=0&limit=200`)).json()
  const persisted = chunks.filter(source => source.locator.calculation_schema === 1)
  expect(persisted).toHaveLength(2)
  expect(new Set(persisted.map(source => source.locator.calculation_scope))).toEqual(new Set(['document', 'current_filter']))
  await page.reload()
  const preview = await (await page.request.get(`/api/v1/documents/${doc.id}/preview`)).json()
  expect(preview.blocks.some(block => block.locator.calculation_schema === 1 && block.locator.filter?.value === 'Проект 237')).toBeTruthy()
  await page.setViewportSize({ width: 390, height: 844 })
  const mobileLayout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    pageWidth: document.documentElement.scrollWidth,
  }))
  expect(mobileLayout.pageWidth).toBeLessThanOrEqual(mobileLayout.viewportWidth)
  await expect(page.getByLabel('Фильтр таблицы')).toBeVisible()
  await expect(page.locator('.csv-calculation-panel > summary')).toBeVisible()
})

test('Table viewer resets filters, sorting and calculations when a new document is opened', async ({ page }) => {
  await upload(page, 'large.csv')
  await page.getByRole('button', { name: 'Сортировать по столбцу Часы по возрастанию' }).click()
  await page.getByLabel('Столбец фильтра').selectOption('0')
  await page.getByLabel('Значение фильтра').fill('Проект 237')
  await page.getByRole('button', { name: 'Применить', exact: true }).click()
  await page.locator('.csv-calculation-panel > summary').click()
  await page.getByRole('button', { name: 'Рассчитать', exact: true }).click()
  await expect(page.locator('.csv-calculation-scope')).toHaveCount(2)

  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/v1/documents') && response.request().method() === 'POST')
  await page.getByLabel('Выберите документ', { exact: true }).setInputFiles(path.join(fixtures, 'sample.csv'))
  const response = await uploaded
  expect(response.status(), await response.text()).toBe(202)
  const document = await response.json()
  await expect.poll(async () => (await (await page.request.get(`/api/v1/documents/${document.id}`)).json()).status).toBe('ready')
  await expect(page.getByRole('heading', { name: 'sample.csv', exact: true })).toBeVisible()

  await expect(page.locator('.csv-filter-summary')).toHaveCount(0)
  await expect(page.locator('.csv-calculation-context')).toHaveCount(0)
  await expect(page.locator('.csv-table-footer')).toContainText('Показано 3 из 3 строк')
  await expect(page.locator('.original-csv-table thead th[aria-sort="ascending"]')).toHaveCount(0)
})

test('export dialog downloads selected answers as Markdown and saved analysis as PDF', async ({ page }) => {
  await upload(page, 'sample.txt')
  await page.setViewportSize({ width: 390, height: 844 })
  const openExport = page.getByRole('button', { name: 'Экспорт', exact: true })
  await expect(openExport).toBeVisible()
  await openExport.click()
  const dialog = page.getByRole('dialog', { name: 'Экспорт документа' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('не отправляет новый запрос модели')).toBeVisible()
  const mobileLayout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    pageWidth: document.documentElement.scrollWidth,
  }))
  const dialogBox = await dialog.boundingBox()
  expect(mobileLayout.pageWidth).toBeLessThanOrEqual(mobileLayout.viewportWidth)
  expect(dialogBox?.x).toBeGreaterThanOrEqual(0)
  expect((dialogBox?.x ?? 0) + (dialogBox?.width ?? Infinity)).toBeLessThanOrEqual(mobileLayout.viewportWidth)

  await dialog.locator('input[name="export-scope"][value="selected_answers"]').check()
  const answers = dialog.locator('.export-answer-option input[type="checkbox"]')
  await expect(answers).toHaveCount(7)
  await answers.nth(1).uncheck()
  await answers.nth(2).uncheck()
  await answers.nth(3).uncheck()
  await answers.nth(4).uncheck()
  await answers.nth(5).uncheck()
  await answers.nth(6).uncheck()
  await dialog.locator('input[name="export-format"][value="markdown"]').check()
  const markdownDownloadPromise = page.waitForEvent('download')
  await dialog.getByRole('button', { name: 'Скачать Markdown' }).click()
  const markdownDownload = await markdownDownloadPromise
  expect(markdownDownload.suggestedFilename()).toMatch(/\.md$/)
  const markdownContent = await readFile(await markdownDownload.path(), 'utf8')
  expect(markdownContent).toContain('## Выбранные ответы')
  expect(markdownContent).toContain('Источники')

  await page.setViewportSize({ width: 1440, height: 900 })
  await openExport.click()
  const pdfDialog = page.getByRole('dialog', { name: 'Экспорт документа' })
  await pdfDialog.locator('input[name="export-format"][value="pdf"]').check()
  const pdfDownloadPromise = page.waitForEvent('download')
  await pdfDialog.getByRole('button', { name: 'Скачать PDF' }).click()
  const pdfDownload = await pdfDownloadPromise
  expect(pdfDownload.suggestedFilename()).toMatch(/\.pdf$/)
  const pdfContent = await readFile(await pdfDownload.path())
  expect(pdfContent.subarray(0, 5).toString()).toBe('%PDF-')
})

test('Citation loads and highlights a matching CSV range beyond the first table page', async ({ page }) => {
  const doc = await upload(page, 'large.csv')
  const seeded = await page.request.post('/api/v1/__e2e/citation', { data: { document_id: doc.id, row: 238 } })
  expect(seeded.ok()).toBeTruthy()
  await page.reload()
  await showChat(page)
  await page.getByLabel('Тип фильтра').selectOption('text')
  await page.getByLabel('Значение фильтра').fill('Проект 1')
  await page.getByRole('button', { name: 'Применить', exact: true }).click()
  const citation = page.locator('.assistant-message .inline-citation').last()
  await expect(citation).toBeVisible()
  await citation.click()

  await expect(page.locator('.csv-table-notice')).toContainText('Фильтр сброшен')
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
