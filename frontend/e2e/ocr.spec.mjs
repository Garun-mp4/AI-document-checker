import { test, expect, provider, upload, showChat, originalVisible } from './helpers.mjs'

const documentIds = new Set()

test.beforeEach(async ({ request }) => provider(request))
test.afterEach(async ({ request }) => {
  for (const id of documentIds) {
    const response = await request.delete(`/api/v1/documents/${id}`)
    expect([204, 404]).toContain(response.status())
  }
  documentIds.clear()
})

test('OCR settings reprocess selected pages into a new version and retain prior chat citations', async ({ page, request }) => {
  test.setTimeout(240_000)
  const doc = await upload(page, 'mixed.pdf')
  documentIds.add(doc.id)
  const initialDocument = await (await request.get(`/api/v1/documents/${doc.id}`)).json()
  const originalBytes = await (await request.get(`/api/v1/documents/${doc.id}/file`)).body()
  const oldVersion = initialDocument.active_version
  const oldChunks = await (await request.get(`/api/v1/documents/${doc.id}/chunks?limit=200`)).json()
  const oldPageFour = oldChunks.find((chunk) => chunk.locator.page === 4 && chunk.locator.ocr === true)
  expect(oldPageFour).toBeTruthy()

  await showChat(page)
  await page.getByLabel('Сообщение для чата', { exact: true }).fill('Перескажи распознанный текст')
  await page.getByRole('button', { name: 'Отправить вопрос', exact: true }).click()
  await expect(page.locator('.assistant-message .inline-citation').first()).toBeVisible()
  const chat = (await (await request.get('/api/v1/chats')).json()).find((item) => item.document_id === doc.id)
  expect(chat).toBeTruthy()
  await expect.poll(async () => {
    const messages = await (await request.get(`/api/v1/chats/${chat.id}/messages`)).json()
    return messages.some((message) => message.role === 'assistant' && message.citations?.length > 0)
  }, { timeout: 15_000, message: 'The streamed answer and its citations are persisted before testing a reprocess' }).toBeTruthy()
  const priorMessages = await (await request.get(`/api/v1/chats/${chat.id}/messages`)).json()
  const priorAssistantMessage = priorMessages.find((message) => message.role === 'assistant')
  const priorCitationIds = priorAssistantMessage.citations.map((source) => source.id)
  expect(priorCitationIds.length).toBeGreaterThan(0)

  await provider(request, { hold_stage: 'ocr' })
  const settings = page.getByTestId('ocr-panel')
  await settings.getByRole('button', { name: /Настроить OCR/ }).click()
  await expect(page.getByTestId('ocr-settings')).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  const chatToggle = page.getByRole('button', { name: 'Свернуть чат', exact: true })
  if (await chatToggle.count()) await chatToggle.click()
  await expect(page.getByTestId('ocr-settings')).toBeVisible()
  const mobileOverflow = await page.evaluate(() => {
    const settingsPanel = document.querySelector('[data-testid="ocr-settings"]')
    return document.documentElement.scrollWidth > innerWidth || Boolean(settingsPanel && settingsPanel.scrollWidth > settingsPanel.clientWidth + 1)
  })
  expect(mobileOverflow, 'OCR controls fit a narrow phone viewport without horizontal overflow').toBeFalsy()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('radio', { name: /Только выбранные страницы/ }).check()
  await page.locator('.ocr-page-option').filter({ hasText: 'Страница 4' }).locator('input').uncheck()
  await page.getByLabel('Язык распознавания').selectOption('eng')
  await page.getByLabel('Качество').selectOption('high')
  await expect(page.getByTestId('ocr-page-status-list')).toContainText('Страница 3')

  await settings.getByRole('button', { name: 'Повторить OCR', exact: true }).click()
  const confirmation = page.getByRole('dialog', { name: 'Создать новую версию OCR?' })
  await expect(confirmation).toBeVisible()
  await expect(confirmation).toContainText('Оригинал документа и история чата сохранятся')
  await expect(confirmation).toContainText('Английский · 300 DPI · 1 стр.')
  await confirmation.getByRole('button', { name: 'Создать версию' }).click()

  await expect.poll(async () => {
    const jobs = await (await request.get(`/api/v1/documents/${doc.id}/jobs`)).json()
    return jobs.find((job) => job.version > oldVersion && job.stage === 'ocr' && ['running', 'cancelling'].includes(job.state)) || null
  }, { timeout: 30_000, message: 'The retry reaches the OCR stage before cancellation' }).toBeTruthy()
  const runningJobs = await (await request.get(`/api/v1/documents/${doc.id}/jobs`)).json()
  const heldOcrJob = runningJobs.find((job) => job.version > oldVersion && job.stage === 'ocr' && ['running', 'cancelling'].includes(job.state))
  expect(heldOcrJob).toBeTruthy()
  const duplicate = await request.post(`/api/v1/documents/${doc.id}/ocr/reprocess`, {
    data: { language: 'eng', quality: 'high', pages: [3] },
  })
  expect(duplicate.status()).toBe(409)
  await expect((await request.post(`/api/v1/documents/${doc.id}/cancel`)).status()).toBe(202)
  await provider(request)
  await expect.poll(async () => {
    const jobs = await (await request.get(`/api/v1/documents/${doc.id}/jobs`)).json()
    return jobs.find((job) => job.id === heldOcrJob.id)?.state
  }, { timeout: 30_000, message: 'The selected-page OCR job is cancelled cleanly' }).toBe('cancelled')
  const afterCancellation = await (await request.get(`/api/v1/documents/${doc.id}`)).json()
  expect(afterCancellation.active_version).toBe(oldVersion)
  expect(afterCancellation.metadata.pdf_page_map).toEqual(initialDocument.metadata.pdf_page_map)
  const chunksAfterCancellation = await (await request.get(`/api/v1/documents/${doc.id}/chunks?limit=200`)).json()
  expect(chunksAfterCancellation.map((chunk) => chunk.id).sort()).toEqual(oldChunks.map((chunk) => chunk.id).sort())

  await expect(settings.getByRole('button', { name: 'Повторить OCR', exact: true })).toBeEnabled()
  await settings.getByRole('button', { name: 'Повторить OCR', exact: true }).click()
  const retryConfirmation = page.getByRole('dialog', { name: 'Создать новую версию OCR?' })
  await retryConfirmation.getByRole('button', { name: 'Создать версию' }).click()
  await expect.poll(async () => {
    const [documentResponse, jobsResponse] = await Promise.all([
      request.get(`/api/v1/documents/${doc.id}`),
      request.get(`/api/v1/documents/${doc.id}/jobs`),
    ])
    const document = await documentResponse.json()
    const jobs = await jobsResponse.json()
    const retry = jobs.filter((job) => job.version > oldVersion).sort((left, right) => right.version - left.version)[0]
    return document.active_version > oldVersion && retry?.state === 'succeeded'
  }, { timeout: 150_000, message: 'A successful OCR retry publishes a new version' }).toBeTruthy()
  const newVersionDocument = await (await request.get(`/api/v1/documents/${doc.id}`)).json()
  expect(newVersionDocument.ocr_language).toBe('eng')
  expect(newVersionDocument.metadata.ocr_settings).toMatchObject({ language: 'eng', quality: 'high', dpi: 300 })
  expect((await (await request.get(`/api/v1/documents/${doc.id}/file`)).body()).equals(originalBytes)).toBeTruthy()

  const newChunks = await (await request.get(`/api/v1/documents/${doc.id}/chunks?limit=200`)).json()
  const newPageFour = newChunks.find((chunk) => chunk.locator.page === 4 && chunk.locator.ocr === true)
  expect(newPageFour).toBeTruthy()
  expect(newPageFour.text).toBe(oldPageFour.text)
  expect(newPageFour.locator.ocr_map.word_boxes).toEqual(oldPageFour.locator.ocr_map.word_boxes)

  const restoredMessages = await (await request.get(`/api/v1/chats/${chat.id}/messages`)).json()
  const restoredAssistantMessage = restoredMessages.find((message) => message.role === 'assistant')
  expect(restoredAssistantMessage.citations.map((source) => source.id)).toEqual(priorCitationIds)
  expect(restoredAssistantMessage.citations.every((source) => source.text.length > 0)).toBeTruthy()
  await page.reload()
  await expect(page.locator('.insight-card')).toHaveCount(7)
  await showChat(page)
  await expect(page.locator('.assistant-message .inline-citation').first()).toBeVisible()
  await page.locator('.assistant-message .inline-citation').first().click()
  await expect(page.locator('.preview-source-callout')).toContainText('Выбран источник')
  await originalVisible(page, 'pdf')
})

test('Text-only PDFs show an empty automatic OCR scope but allow manual page selection', async ({ page }) => {
  const doc = await upload(page, 'sample.pdf')
  documentIds.add(doc.id)
  const settings = page.getByTestId('ocr-panel')
  await settings.getByRole('button', { name: /Настроить OCR/ }).click()
  await expect(page.getByTestId('ocr-settings')).toBeVisible()
  await expect(page.getByRole('radio', { name: /Все страницы, для которых нужен OCR \(0\)/ })).toBeVisible()
  await expect(settings.getByRole('button', { name: 'Повторить OCR', exact: true })).toBeDisabled()
  await expect(page.getByText('Страниц, которым требуется OCR, не найдено.')).toBeVisible()
  await page.getByRole('radio', { name: /Только выбранные страницы/ }).check()
  await expect(settings.getByRole('button', { name: 'Повторить OCR', exact: true })).toBeEnabled()
  await expect(page.locator('.ocr-page-option input[type="checkbox"]:disabled')).toHaveCount(0)
})
