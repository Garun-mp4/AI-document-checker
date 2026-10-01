import { test, expect, upload, provider, originalVisible } from './helpers.mjs'

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
