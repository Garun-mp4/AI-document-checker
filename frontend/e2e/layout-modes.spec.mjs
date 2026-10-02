import { test, expect, upload, originalVisible, provider } from './helpers.mjs'

test.beforeEach(async ({ request }) => provider(request))

test('workspace layout is user-selectable, responsive, persistent, and preserves citations', async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => localStorage.setItem('document-checker-analysis-layout', 'invalid-value'))
  await page.reload()
  await upload(page, 'sample.docx')
  await originalVisible(page, 'docx')
  const layout = page.locator('.document-analysis-layout')
  const layoutSelect = page.getByRole('combobox', { name: 'Расположение' })
  const modeOption = label => page.getByRole('option').filter({ has: page.getByText(label, { exact: true }) })
  const chooseMode = async label => {
    await layoutSelect.click()
    await modeOption(label).click()
  }

  await expect(layoutSelect).toContainText('Авто')
  await page.setViewportSize({ width: 1920, height: 1080 })
  await expect.poll(() => layout.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(2)

  await page.setViewportSize({ width: 1280, height: 720 })
  await expect.poll(() => layout.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(1)

  await layoutSelect.focus()
  await page.keyboard.press('ArrowDown')
  await expect(modeOption('Авто')).toHaveAttribute('data-highlighted', '')
  await page.keyboard.press('ArrowDown')
  await expect(modeOption('Документ сверху')).toHaveAttribute('data-highlighted', '')
  await page.keyboard.press('Enter')
  await expect(layoutSelect).toContainText('Документ сверху')
  await expect(layout).toHaveAttribute('data-layout-mode', 'stacked')
  await expect.poll(() => layout.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(1)

  await chooseMode('Документ слева')
  await expect(layoutSelect).toContainText('Документ слева')
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
    if (width === 430 || width === 390) {
      await layoutSelect.click()
      const listbox = page.getByRole('listbox')
      await expect(listbox.getByRole('option')).toHaveCount(3)
      const popup = await listbox.boundingBox()
      expect(popup, `layout selector popup is rendered at ${width}px`).toBeTruthy()
      expect(popup.x).toBeGreaterThanOrEqual(0)
      expect(popup.x + popup.width).toBeLessThanOrEqual(width + 1)
      for (const optionCopy of await listbox.locator('.workspace-layout-option-copy').all()) {
        const dimensions = await optionCopy.evaluate(element => ({ scrollWidth: element.scrollWidth, clientWidth: element.clientWidth }))
        expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1)
      }
      await page.keyboard.press('Escape')
    }
  }

  await page.reload()
  await expect(page.getByRole('combobox', { name: 'Расположение' })).toContainText('Документ слева')
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

  await chooseMode('Документ сверху')
  await expect(layoutSelect).toContainText('Документ сверху')
  await expect.poll(async () => {
    const [viewerBox, insightsBox] = await Promise.all([viewer.boundingBox(), insights.boundingBox()])
    return Boolean(viewerBox && insightsBox && insightsBox.y >= viewerBox.y + viewerBox.height)
  }).toBeTruthy()
  await expect(page.locator('#document-original-viewer .source-match').first()).toBeVisible()

  await chooseMode('Документ слева')
  await expect(layoutSelect).toContainText('Документ слева')
  await page.setViewportSize({ width: 1920, height: 1080 })
  await upload(page, 'sample.csv')
  await originalVisible(page, 'csv')
  const tableLayout = page.locator('.document-analysis-layout')
  await expect(page.getByRole('combobox', { name: 'Расположение' })).toContainText('Документ слева')
  await expect(tableLayout).toHaveClass(/is-table-layout/)
  await expect.poll(() => tableLayout.evaluate(element => getComputedStyle(element).display)).toBe('block')
  await expect(page.getByRole('status').filter({ hasText: 'табличных документов' })).toBeVisible()
  await expect(page.locator('.original-csv-table')).toBeVisible()
})

test('workspace layout selector closes on Escape and outside click and exposes its selected option', async ({ page }) => {
  await upload(page, 'sample.txt')
  const trigger = page.getByRole('combobox', { name: 'Расположение' })
  const listbox = page.getByRole('listbox')

  await trigger.focus()
  await page.keyboard.press('Enter')
  await expect(trigger).toHaveAttribute('aria-expanded', 'true')
  await expect(listbox.getByRole('option')).toHaveCount(3)
  await expect(listbox.getByRole('option').filter({ has: page.getByText('Авто', { exact: true }) })).toHaveCSS('outline-width', '2px')
  const listboxId = await trigger.getAttribute('aria-controls')
  expect(listboxId).toBeTruthy()
  await expect(page.locator(`#${listboxId}`)).toHaveAttribute('role', 'listbox')
  await expect(listbox.getByRole('option').filter({ has: page.getByText('Авто', { exact: true }) })).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('Escape')
  await expect(trigger).toHaveAttribute('aria-expanded', 'false')
  await expect(listbox).toHaveCount(0)

  await trigger.click()
  await expect(listbox).toBeVisible()
  await page.locator('.document-facts').click()
  await expect(trigger).toHaveAttribute('aria-expanded', 'false')
  await expect(listbox).toHaveCount(0)
})

test('small desktop height keeps workspace panels usable without page-level scrolling', async ({ page }) => {
  const consoleErrors = []
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  await page.setViewportSize({ width: 1280, height: 600 })
  await upload(page, 'sample.txt')
  const shell = page.locator('.app-shell')
  await expect.poll(() => shell.evaluate(element => Math.round(element.getBoundingClientRect().height))).toBe(600)
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1)).toBeTruthy()

  const libraryToggle = page.getByRole('button', { name: 'Свернуть библиотеку', exact: true })
  await libraryToggle.click()
  await expect.poll(() => page.locator('#chat-library').evaluate(element => Math.round(element.getBoundingClientRect().width))).toBe(72)
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1)).toBeTruthy()

  const chatToggle = page.locator('.chat-visibility-toggle')
  await expect(chatToggle).toBeVisible()
  await chatToggle.click()
  await expect(shell).toHaveClass(/chat-hidden/)
  await expect(chatToggle).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await chatToggle.click()
  await expect(shell).not.toHaveClass(/chat-hidden/)
  await expect(page.locator('.chat-panel')).toBeVisible()
  expect(consoleErrors, 'No console errors while collapsing and restoring workspace panels').toEqual([])
})
