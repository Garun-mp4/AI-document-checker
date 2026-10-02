import { readFile } from 'node:fs/promises'
import { test, expect, upload, provider } from './helpers.mjs'

test.beforeEach(async ({ request }) => provider(request))

test('Bookmarks and supplementary analyses persist by version and export only selected results', async ({ page, request }) => {
  const consoleErrors = []
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  const document = await upload(page, 'sample.txt')
  await page.getByRole('button', { name: 'Свернуть очередь загрузки', exact: true }).click()
  const mainCitation = page.locator('.insight-card .citation-chip').first()
  await expect(mainCitation).toBeVisible()
  const sourceId = await mainCitation.getAttribute('data-source-id')
  expect(sourceId).toBeTruthy()
  await mainCitation.click()
  await expect(page.locator('.preview-source-callout')).toContainText('Выбран источник')
  await expect(page.locator('#document-original-viewer .source-text-line mark').first()).toBeVisible()

  const bookmarkCreated = page.waitForResponse(response =>
    response.url().endsWith(`/api/v1/documents/${document.id}/bookmarks`) &&
    response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Добавить источник', exact: true }).click()
  const bookmarkResponse = await bookmarkCreated
  expect(bookmarkResponse.status(), await bookmarkResponse.text()).toBe(201)
  await expect(page.locator('.bookmark-entry')).toHaveCount(1)
  const bookmarksResponse = await request.get(`/api/v1/documents/${document.id}/bookmarks`)
  expect(bookmarksResponse.ok()).toBeTruthy()
  const [bookmark] = await bookmarksResponse.json()
  expect(bookmark.source.id).toBe(sourceId)
  expect(bookmark.source_version).toBeGreaterThanOrEqual(0)

  const duplicate = await request.post(`/api/v1/documents/${document.id}/bookmarks`, {
    data: { source_id: sourceId, source_version: bookmark.source_version },
  })
  expect(duplicate.status()).toBe(409)
  const staleSource = await request.post(`/api/v1/documents/${document.id}/bookmarks`, {
    data: { source_id: sourceId, source_version: bookmark.source_version + 1 },
  })
  expect(staleSource.status()).toBe(409)

  await expect(page.locator('.bookmark-entry')).toHaveCount(1)
  await page.getByRole('button', { name: 'Добавить заметку' }).click()
  await page.getByRole('textbox', { name: 'Заметка к закладке' }).fill('Проверить первоисточник')
  await page.getByRole('button', { name: 'Сохранить заметку' }).click()
  await expect(page.locator('.bookmark-note')).toContainText('Проверить первоисточник')

  const panel = page.getByTestId('additional-analysis-panel')
  await expect(panel.locator('.additional-analysis-result')).toHaveCount(0)
  for (const [mode, label] of [
    ['brief', 'Кратко'],
    ['detailed', 'Подробно'],
    ['tasks', 'Задачи'],
    ['risks', 'Риски и неясности'],
  ]) {
    await panel.getByRole('button', { name: label, exact: true }).click()
    const result = panel.locator(`.additional-analysis-result[data-analysis-mode="${mode}"]`).last()
    await expect(result).toBeVisible()
    await expect(result.locator('.citation-chip')).toHaveCount(1)
    await expect(result.locator('.additional-analysis-version')).toContainText('источники v')
  }
  await panel.getByRole('button', { name: 'Кратко', exact: true }).click()
  await expect(panel.locator('.additional-analysis-result[data-analysis-mode="brief"]')).toHaveCount(2)
  const savedAnalyses = await (await request.get(`/api/v1/documents/${document.id}/analysis/additional`)).json()
  expect(savedAnalyses.map(item => item.mode).sort()).toEqual(['brief', 'brief', 'detailed', 'risks', 'tasks'])
  expect(savedAnalyses.every(item => item.analysis_version === 1 && item.source_version === bookmark.source_version)).toBeTruthy()
  expect(savedAnalyses.every(item => item.model === 'gpt-6-luna' && item.reasoning_effort === 'medium')).toBeTruthy()

  const taskResult = panel.locator('.additional-analysis-result[data-analysis-mode="tasks"]').first()
  await taskResult.locator('.citation-chip').click()
  await expect(page.locator('.preview-source-callout')).toContainText('Выбран источник')

  await page.getByRole('button', { name: /Экспорт/ }).click()
  const extraPicker = page.locator('[aria-label="Выбор дополнительных результатов для экспорта"]')
  await expect(extraPicker.locator('input[type="checkbox"]')).toHaveCount(5)
  await extraPicker.getByRole('button', { name: 'Снять выбор', exact: true }).click()
  await extraPicker.locator('label').filter({ hasText: 'Задачи' }).locator('input').check()
  await page.locator('input[name="export-format"][value="markdown"]').check()
  const downloadWait = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Скачать Markdown', exact: true }).click()
  const download = await downloadWait
  const report = await readFile(await download.path(), 'utf8')
  expect(report).toContain('## Дополнительные результаты')
  expect(report).toContain('### Задачи')
  expect(report).toContain('Алексей Пример')
  expect(report).not.toContain('### Кратко')
  expect(report).not.toContain('### Подробно')

  await page.reload()
  await expect(page.locator('.additional-analysis-result')).toHaveCount(5)
  await page.getByRole('button', { name: 'Закладки' }).click()
  await expect(page.locator('.bookmark-entry')).toHaveCount(1)
  await expect(page.locator('.bookmark-note')).toContainText('Проверить первоисточник')
  await page.locator('.bookmark-source').click()
  await expect(page.locator('.preview-source-callout')).toContainText('Выбран источник')
  await expect(page.locator('#document-original-viewer .source-text-line mark').first()).toBeVisible()

  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await expect(page.locator('.additional-analysis-actions .button').first()).toBeVisible()
  await expect(page.locator('.bookmark-add-button')).toBeVisible()
  expect(consoleErrors, 'No browser console errors in bookmark, analysis, reload or export flow').toEqual([])

  await page.getByRole('button', { name: 'Удалить закладку' }).click()
  await expect(page.locator('.bookmark-entry')).toHaveCount(0)
  expect(await (await request.get(`/api/v1/documents/${document.id}/bookmarks`)).json()).toEqual([])
})
