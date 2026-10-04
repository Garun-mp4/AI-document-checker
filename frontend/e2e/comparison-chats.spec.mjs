import { test, expect, upload, provider, showChat } from './helpers.mjs'

test.beforeEach(async ({ request }) => provider(request))

test('comparison chat pins explicit document versions, keeps context history and opens citations in the right original', async ({ page, request }) => {
  const first = await upload(page, 'sample.txt', 'ready', 'm21-first.txt')
  const second = await upload(page, 'sample.md', 'ready', 'm21-second.md')
  const third = await upload(page, 'sample.csv', 'ready', 'm21-third.csv')
  let comparisonId

  try {
    const duplicateSelection = await request.post('/api/v1/chats/comparison', { data: { document_ids: [first.id, first.id] } })
    expect(duplicateSelection.status()).toBe(422)
    const tooFewSources = await request.post('/api/v1/chats/comparison', { data: { document_ids: [first.id] } })
    expect(tooFewSources.status()).toBe(422)

    await page.getByRole('button', { name: 'Сравнить документы', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Выберите источники' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText('Выбрано: 0 из 5 · минимум 2')).toBeVisible()
    await dialog.getByRole('checkbox', { name: 'Выбрать m21-first.txt' }).check()
    await dialog.getByRole('checkbox', { name: 'Выбрать m21-second.md' }).check()
    await expect(dialog.getByRole('button', { name: 'Начать сравнение' })).toBeEnabled()
    await dialog.getByRole('button', { name: 'Начать сравнение' }).click()
    await expect(dialog).toHaveCount(0)

    await expect(page.getByRole('heading', { name: 'Сравнивайте документы в одном чате.' })).toBeVisible()
    await expect(page.locator('.chat-panel-header').getByText('Сравнение документов', { exact: true })).toBeVisible()
    await expect(page.locator('.comparison-workspace-source')).toHaveCount(2)
    const summaries = await (await request.get('/api/v1/chats/library')).json()
    const summary = summaries.items.find(item => item.scope === 'comparison')
    expect(summary).toBeTruthy()
    comparisonId = summary.id
    expect(summary.documents.map(item => item.id)).toEqual([first.id, second.id])
    expect(summary.documents.every(item => item.source_version > 0 && item.status === 'ready')).toBeTruthy()

    const composer = page.getByLabel('Сообщение для чата', { exact: true })
    await composer.fill('Сравни автора и срок в выбранных документах')
    await page.getByRole('button', { name: 'Отправить вопрос' }).click()
    await expect.poll(async () => {
      const messages = await (await request.get(`/api/v1/chats/${comparisonId}/messages`)).json()
      return messages.find(message => message.role === 'assistant')?.generation_status
    }, { timeout: 30_000 }).toBe('complete')
    let savedMessages = await (await request.get(`/api/v1/chats/${comparisonId}/messages`)).json()
    const oldAnswer = savedMessages.find(message => message.role === 'assistant')
    expect(oldAnswer.content).toContain('По каждому документу')
    expect(oldAnswer.content).toContain('Сопоставление')
    expect(new Set(oldAnswer.citations.map(source => source.document_id))).toEqual(new Set([first.id, second.id]))
    expect(oldAnswer.citations.every(source => source.document_filename && source.source_version > 0)).toBeTruthy()
    const providerState = await (await request.post('/api/v1/__e2e/provider', { data: {} })).json()
    expect(providerState.last_request_metadata.comparison_documents_count).toBe(2)
    expect(providerState.last_request_metadata.comparison_source_document_ids).toEqual([first.id, second.id].sort())

    await page.getByRole('button', { name: 'Документы', exact: true }).click()
    const editDialog = page.getByRole('dialog', { name: 'Выберите источники' })
    await expect(editDialog.getByRole('checkbox', { name: 'Выбрать m21-first.txt' })).toBeChecked()
    await editDialog.getByRole('checkbox', { name: 'Выбрать m21-first.txt' }).uncheck()
    await editDialog.getByRole('checkbox', { name: 'Выбрать m21-third.csv' }).check()
    await editDialog.getByRole('button', { name: 'Обновить источники' }).click()
    await expect(editDialog).toHaveCount(0)

    const updated = await (await request.get(`/api/v1/chats/${comparisonId}`)).json()
    expect(updated.scope).toBe('comparison')
    expect(updated.revision).toBe(2)
    expect(updated.context_epoch).toBe(1)
    expect(updated.documents.map(item => item.id)).toEqual([second.id, third.id])
    savedMessages = await (await request.get(`/api/v1/chats/${comparisonId}/messages`)).json()
    expect(savedMessages.some(message => message.id === oldAnswer.id && message.context_epoch === 0)).toBeTruthy()

    await page.reload()
    await expect(page.getByRole('heading', { name: 'Сравнивайте документы в одном чате.' })).toBeVisible()
    await expect(page.locator('.comparison-workspace-source')).toHaveCount(2)
    await expect(page.locator('.chat-context-divider')).toContainText('История выше сохранена')

    await page.getByLabel('Сообщение для чата', { exact: true }).fill('Проверь только новый набор источников')
    await page.getByRole('button', { name: 'Отправить вопрос' }).click()
    await expect.poll(async () => {
      const messages = await (await request.get(`/api/v1/chats/${comparisonId}/messages`)).json()
      return messages.filter(message => message.role === 'assistant' && message.context_epoch === 1).at(-1)?.generation_status
    }, { timeout: 30_000 }).toBe('complete')
    const updatedProviderState = await (await request.post('/api/v1/__e2e/provider', { data: {} })).json()
    expect(updatedProviderState.last_request_metadata.comparison_source_document_ids).toEqual([second.id, third.id].sort())

    const historicalCitation = page.locator('.message-sources button').filter({ hasText: 'm21-first.txt' }).first()
    await expect(historicalCitation).toBeVisible()
    await historicalCitation.click()
    await expect(page.getByRole('heading', { name: 'm21-first.txt', exact: true })).toBeVisible()
    await expect(page.locator('#document-original-viewer')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()

    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  } finally {
    if (comparisonId) await request.delete(`/api/v1/chats/${comparisonId}`)
    await request.delete(`/api/v1/documents/${first.id}`)
    await request.delete(`/api/v1/documents/${second.id}`)
    await request.delete(`/api/v1/documents/${third.id}`)
  }
})

test('deleting a selected source leaves a recoverable comparison chat', async ({ page, request }) => {
  const first = await upload(page, 'sample.txt', 'ready', 'm21-delete-first.txt')
  const second = await upload(page, 'sample.md', 'ready', 'm21-delete-second.md')
  const replacement = await upload(page, 'sample.csv', 'ready', 'm21-delete-replacement.csv')
  let comparisonId

  try {
    await page.getByRole('button', { name: 'Сравнить документы', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Выберите источники' })
    await dialog.getByRole('checkbox', { name: `Выбрать ${first.filename}` }).check()
    await dialog.getByRole('checkbox', { name: `Выбрать ${second.filename}` }).check()
    await dialog.getByRole('button', { name: 'Начать сравнение' }).click()

    const library = await (await request.get('/api/v1/chats/library')).json()
    comparisonId = library.items.find(item => item.scope === 'comparison')?.id
    expect(comparisonId).toBeTruthy()
    const deletion = await request.delete(`/api/v1/documents/${first.id}`)
    expect(deletion.status()).toBe(204)

    await page.reload()
    await expect(page.getByRole('heading', { name: 'Обновите набор источников' })).toBeVisible()
    await expect(page.getByLabel('Сообщение для чата', { exact: true })).toBeDisabled()
    await page.getByRole('button', { name: 'Документы', exact: true }).click()
    const editDialog = page.getByRole('dialog', { name: 'Выберите источники' })
    await expect(editDialog.getByRole('checkbox', { name: `Выбрать ${first.filename}` })).toHaveCount(0)
    await expect(editDialog.getByRole('checkbox', { name: `Выбрать ${second.filename}` })).toBeChecked()
    await editDialog.getByRole('checkbox', { name: `Выбрать ${replacement.filename}` }).check()
    await editDialog.getByRole('button', { name: 'Обновить источники' }).click()

    await expect(page.getByRole('heading', { name: 'Что сопоставить?' })).toBeVisible()
    const recovered = await (await request.get(`/api/v1/chats/${comparisonId}`)).json()
    expect(recovered.documents.map(item => item.id)).toEqual([second.id, replacement.id])
    expect(recovered.revision).toBe(2)
    expect(recovered.context_epoch).toBe(1)
  } finally {
    if (comparisonId) await request.delete(`/api/v1/chats/${comparisonId}`)
    await request.delete(`/api/v1/documents/${first.id}`)
    await request.delete(`/api/v1/documents/${second.id}`)
    await request.delete(`/api/v1/documents/${replacement.id}`)
  }
})

test('comparison picker distinguishes duplicate long filenames and fits a mobile viewport', async ({ page, request }) => {
  const duplicateName = 'm21-reference-document-'.repeat(3) + 'shared.txt'
  const first = await upload(page, 'sample.txt', 'ready', duplicateName)
  const second = await upload(page, 'sample.txt', 'ready', duplicateName)
  let comparisonId

  try {
    await page.getByRole('button', { name: 'Сравнить документы', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Выберите источники' })
    const firstCopy = dialog.getByRole('checkbox', { name: `Выбрать ${duplicateName}, документ 1 из 2` })
    const secondCopy = dialog.getByRole('checkbox', { name: `Выбрать ${duplicateName}, документ 2 из 2` })
    await expect(firstCopy).toBeVisible()
    await expect(secondCopy).toBeVisible()
    await firstCopy.check()
    await secondCopy.check()
    const createResponsePromise = page.waitForResponse(response =>
      response.url().endsWith('/api/v1/chats/comparison') &&
      response.request().method() === 'POST' &&
      response.status() === 201,
    )
    await dialog.getByRole('button', { name: 'Начать сравнение' }).click()

    const createResponse = await createResponsePromise
    comparisonId = (await createResponse.json()).id
    expect(comparisonId).toBeTruthy()
    const comparison = await (await request.get(`/api/v1/chats/${comparisonId}`)).json()
    expect(new Set(comparison.documents.map(item => item.id))).toEqual(new Set([first.id, second.id]))

    await page.setViewportSize({ width: 390, height: 844 })
    await showChat(page)
    await page.getByRole('button', { name: 'Документы', exact: true }).click()
    const mobileDialog = page.getByRole('dialog', { name: 'Выберите источники' })
    await expect(mobileDialog.getByRole('checkbox', { name: `Выбрать ${duplicateName}, документ 1 из 2` })).toBeChecked()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  } finally {
    if (comparisonId) await request.delete(`/api/v1/chats/${comparisonId}`)
    await request.delete(`/api/v1/documents/${first.id}`)
    await request.delete(`/api/v1/documents/${second.id}`)
  }
})
