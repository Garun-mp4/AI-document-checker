import { test, expect, upload, originalVisible, provider } from './helpers.mjs'

test.beforeEach(async ({ request }) => provider(request))

test('workspace layout is user-selectable, responsive, persistent, and preserves citations', async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => localStorage.setItem('document-checker-analysis-layout', 'invalid-value'))
  await page.reload()
  await upload(page, 'sample.docx')
  await originalVisible(page, 'docx')
  const layout = page.locator('.document-analysis-layout')
  const splitRadio = page.getByRole('radio', { name: 'Документ слева', exact: true })
  const stackedRadio = page.getByRole('radio', { name: 'Документ сверху', exact: true })
  const autoRadio = page.getByRole('radio', { name: 'Авто', exact: true })

  await expect(autoRadio).toBeChecked()
  await page.setViewportSize({ width: 1920, height: 1080 })
  await expect.poll(() => layout.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(2)

  await page.setViewportSize({ width: 1280, height: 720 })
  await expect.poll(() => layout.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(1)

  await autoRadio.focus()
  await page.keyboard.press('ArrowRight')
  await expect(stackedRadio).toBeChecked()
  await expect(layout).toHaveAttribute('data-layout-mode', 'stacked')
  await expect.poll(() => layout.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(1)

  await page.getByText('Документ слева', { exact: true }).click()
  await expect(splitRadio).toBeChecked()
  await expect(layout).toHaveAttribute('data-layout-mode', 'split')
  await expect(page.getByRole('status').filter({ hasText: 'временно показан сверху' })).toBeVisible()

  for (const [width, height] of [[1440, 900], [1280, 720], [1024, 768], [768, 1024], [430, 932], [390, 844]]) {
    await page.setViewportSize({ width, height })
    const metrics = await layout.evaluate(element => {
      const style = getComputedStyle(element.closest('.workspace-scroll'))
      const availableWidth = element.closest('.workspace-scroll').clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      return { availableWidth, columns: getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length }
    })
    expect(metrics.columns, `split mode at ${width}×${height} adapts to ${Math.round(metrics.availableWidth)}px of workspace width`).toBe(metrics.availableWidth >= 760 ? 2 : 1)
    const fallbackNote = page.getByRole('status').filter({ hasText: 'выбор сохранён' })
    if (metrics.availableWidth < 760) await expect(fallbackNote).toBeVisible()
    else await expect(fallbackNote).toBeHidden()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), `no horizontal overflow at ${width}×${height}`).toBeTruthy()
    if (width === 390) {
      const uploadQueue = page.getByRole('region', { name: 'Очередь загрузки документов' })
      if (await uploadQueue.isVisible()) await uploadQueue.getByRole('button', { name: 'Свернуть очередь загрузки' }).click()
      await expect(page.locator('.insight-card')).toHaveCount(7)
      await page.screenshot({ path: 'test-results/layout-split-mobile.png' })
    }
  }

  await page.reload()
  await expect(page.getByRole('radio', { name: 'Документ слева', exact: true })).toBeChecked()
  await expect(layout).toHaveAttribute('data-layout-mode', 'split')
  await expect(page.locator('.insight-card')).toHaveCount(7)
  await expect.poll(() => layout.evaluate(element => getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length)).toBe(1)

  await page.setViewportSize({ width: 1920, height: 1080 })
  await expect.poll(() => layout.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(2)
  const viewer = page.locator('#document-original-viewer')
  const insights = page.locator('.insights-section')
  await expect.poll(async () => {
    const [viewerBox, insightsBox] = await Promise.all([viewer.boundingBox(), insights.boundingBox()])
    return Boolean(viewerBox && insightsBox && insightsBox.x > viewerBox.x && Math.abs(insightsBox.y - viewerBox.y) < 8)
  }).toBeTruthy()
  await page.screenshot({ path: 'test-results/layout-split-desktop.png' })

  await page.locator('.citation-chip').first().click()
  await expect(page.locator('.preview-source-callout')).toContainText('Выбран источник')
  await expect(page.locator('#document-original-viewer .source-match').first()).toBeVisible()

  await page.getByText('Документ сверху', { exact: true }).click()
  await expect(stackedRadio).toBeChecked()
  await expect.poll(async () => {
    const [viewerBox, insightsBox] = await Promise.all([viewer.boundingBox(), insights.boundingBox()])
    return Boolean(viewerBox && insightsBox && insightsBox.y >= viewerBox.y + viewerBox.height)
  }).toBeTruthy()
  await expect(page.locator('#document-original-viewer .source-match').first()).toBeVisible()

  await page.getByText('Документ слева', { exact: true }).click()
  await expect(page.getByRole('radio', { name: 'Документ слева', exact: true })).toBeChecked()
  await page.setViewportSize({ width: 1920, height: 1080 })
  await upload(page, 'sample.csv')
  await originalVisible(page, 'csv')
  const tableLayout = page.locator('.document-analysis-layout')
  await expect(page.getByRole('radio', { name: 'Документ слева', exact: true })).toBeChecked()
  await expect(tableLayout).toHaveClass(/is-table-layout/)
  await expect.poll(() => tableLayout.evaluate(element => getComputedStyle(element).display)).toBe('block')
  await expect(page.getByRole('status').filter({ hasText: 'табличных документов' })).toBeVisible()
  await expect(page.locator('.original-csv-table')).toBeVisible()
})
