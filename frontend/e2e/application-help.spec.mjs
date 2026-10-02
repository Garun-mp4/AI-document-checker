import { test, expect, provider, upload, showChat } from './helpers.mjs'

test('application-help chat buffers structured output, persists, resolves citations and deletes independently', async ({ page, request }) => {
  await provider(request)
  const documentIdsBefore = (await (await request.get('/api/v1/documents')).json()).map(document => document.id).sort()
  await page.goto('/')
  // The isolated DB is shared across browser tests, so explicitly enter the
  // new-chat workspace even when a previous test left documents in the library.
  await page.getByRole('button', { name: 'Новый чат', exact: true }).click()
  const createdRequest = page.waitForResponse(response => response.url().endsWith('/api/v1/chats/application') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Спросить о работе приложения' }).click()
  const createdResponse = await createdRequest
  const created = await createdResponse.json()
  const appChatId = created.id
  try {
  await expect(page.locator('.chat-panel .chat-title strong')).toHaveText('Помощь по приложению')

  expect(created.document_id).toBeNull()
  const restored = await (await request.get(`/api/v1/chats/${created.id}`)).json()
  expect(restored).toMatchObject({ scope: 'application', document_id: null })

  await provider(request, { mode: 'ready', hold_stream: true, reset_counters: true })
  const input = page.getByRole('textbox', { name: 'Сообщение для чата' })
  await expect(page.locator('#document-chat')).toBeVisible()
  await expect(input).toBeEnabled()
  await input.fill('Как поменять модель?')
  const started = page.waitForResponse(response => response.url().endsWith(`/api/v1/chats/${created.id}/messages`) && response.request().method() === 'POST')
  await input.press('Enter')
  await started
  const assistant = page.locator('.chat-message.assistant-message').last()
  await expect(assistant.getByRole('button', { name: 'Остановить' })).toBeVisible()
  await expect.poll(async () => {
    const response = await request.post('/api/v1/__e2e/provider', { data: { mode: 'ready', hold_stream: true } })
    return (await response.json()).chat_calls
  }).toBe(1)
  await expect(assistant.locator('.message-text')).not.toContainText('{')
  await expect(assistant.locator('.message-text')).not.toContainText('"answer"')

  const heldState = await (await request.post('/api/v1/__e2e/provider', { data: { mode: 'ready', hold_stream: true } })).json()
  expect(heldState.last_request_metadata).toMatchObject({
    document_evidence_count: 0,
    application_evidence_count: expect.any(Number),
    ephemeral: true,
    scope: 'application',
  })
  await assistant.getByRole('button', { name: 'Остановить' }).click()
  await expect(assistant).toHaveClass(/message-interrupted/)
  const interrupted = await (await request.get(`/api/v1/chats/${created.id}/messages`)).json()
  expect(interrupted.at(-1)).toMatchObject({ role: 'assistant', content: '', generation_status: 'interrupted', citations: [] })

  await provider(request, { mode: 'ready' })
  await assistant.getByRole('button', { name: 'Повторить вопрос' }).click()
  await expect(page.locator('.chat-message.assistant-message').last().locator('.message-text')).toContainText('Подтверждённая возможность приложения.')
  const completed = await (await request.get(`/api/v1/chats/${created.id}/messages`)).json()
  const answer = completed.at(-1)
  expect(answer.citations).toHaveLength(1)
  expect(answer.citations[0]).toMatchObject({ source_type: 'application', title: 'Подключение Codex и выбор модели' })
  expect(answer.content).not.toContain('"status"')
  expect(answer.content).toContain('〔1〕')

  await page.locator('.chat-message.assistant-message').last().locator('.message-sources button').click()
  await expect(page.getByRole('dialog', { name: 'Подключение Codex и выбор модели' })).toBeVisible()
  await page.getByRole('button', { name: 'Понятно' }).click()

  const row = page.locator(`[data-chat-id="${created.id}"]`)
  await row.locator('.row-actions-trigger').click()
  await page.getByRole('menuitem', { name: 'Переименовать', exact: true }).click()
  await page.getByRole('textbox', { name: 'Название чата' }).fill('Помощь по модели')
  await page.getByRole('button', { name: 'Сохранить название чата' }).click()
  await expect(row.locator('.document-row-name')).toHaveText('Помощь по модели')
  await row.locator('.row-actions-trigger').click()
  await page.getByRole('menuitem', { name: 'Закрепить чат', exact: true }).click()
  await expect(row).toHaveClass(/is-pinned/)

  await page.reload()
  await expect(page.locator('.chat-panel .chat-title strong')).toHaveText('Помощь по приложению')
  await expect(page.locator('.chat-message.assistant-message').last().locator('.message-text')).toContainText('Подтверждённая возможность приложения.')
  await expect(page.locator(`[data-chat-id="${created.id}"]`)).toHaveClass(/is-pinned/)
  await expect(page.locator(`[data-chat-id="${created.id}"] .document-row-name`)).toHaveText('Помощь по модели')
  await input.fill('Где найти функцию изменения темы интерфейса?')
  await input.press('Enter')
  await expect(page.locator('.chat-message.assistant-message').last().locator('.message-text')).toContainText('не нашёл подтверждённой информации')
  const unknown = (await (await request.get(`/api/v1/chats/${created.id}/messages`)).json()).at(-1)
  expect(unknown.citations).toEqual([])
  expect(unknown.content).not.toContain('〔')
  await row.locator('.row-actions-trigger').click()
  await page.getByRole('menuitem', { name: 'Удалить чат', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Удалить чат помощи?' })).toBeVisible()
  await page.getByRole('button', { name: 'Удалить чат', exact: true }).last().click()
  await expect(row).toHaveCount(0)
  expect((await request.get(`/api/v1/chats/${created.id}`)).status()).toBe(404)
  const documentIdsAfter = (await (await request.get('/api/v1/documents')).json()).map(document => document.id).sort()
  expect(documentIdsAfter).toEqual(documentIdsBefore)
  } finally {
    if ((await request.get(`/api/v1/chats/${appChatId}`)).ok()) await request.delete(`/api/v1/chats/${appChatId}`)
  }
})

test('application-help chat opens and restores its composer on narrow screens', async ({ page, request }) => {
  await provider(request)
  await page.goto('/')
  await page.getByRole('button', { name: 'Новый чат', exact: true }).click()
  const createdRequest = page.waitForResponse(response => response.url().endsWith('/api/v1/chats/application') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Спросить о работе приложения' }).click()
  const created = await (await createdRequest).json()
  try {
    await expect(page.locator('.chat-panel .chat-title strong')).toHaveText('Помощь по приложению')
    await page.setViewportSize({ width: 390, height: 844 })
    const input = page.getByRole('textbox', { name: 'Сообщение для чата' })
    await expect(input).toBeVisible()
    await expect(page.locator('.chat-panel .chat-title strong')).toHaveText('Помощь по приложению')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()

    await page.reload()
    await expect(page.getByRole('textbox', { name: 'Сообщение для чата' })).toBeVisible()
    await expect(page.locator('.chat-panel .chat-title strong')).toHaveText('Помощь по приложению')
  } finally {
    await request.delete(`/api/v1/chats/${created.id}`)
  }
})

test('late document loading cannot replace the selected application-help chat', async ({ page, request }) => {
  await provider(request)
  const document = await upload(page, 'sample.txt')
  const appChat = await (await request.post('/api/v1/chats/application')).json()
  let releaseInsights
  let markInsightsRequested
  const insightsRequested = new Promise(resolve => { markInsightsRequested = resolve })
  const heldInsights = new Promise(resolve => { releaseInsights = resolve })
  const insightsUrl = `**/api/v1/documents/${document.id}/insights`

  try {
    await page.route(insightsUrl, async route => {
      markInsightsRequested()
      await heldInsights
      await route.continue()
    })
    await page.evaluate(documentId => {
      localStorage.setItem('document-checker-selected-chat', documentId)
      localStorage.removeItem('document-checker-selected-application-chat')
      localStorage.removeItem('document-checker-new-chat')
    }, document.id)
    await page.reload()
    await insightsRequested

    const appChatRow = page.locator(`[data-chat-id="${appChat.id}"]`)
    await expect(appChatRow).toBeVisible()
    await appChatRow.locator('.document-select').click()
    await expect(page.locator('.chat-panel .chat-title strong')).toHaveText('Помощь по приложению')
    await expect(page.getByRole('textbox', { name: 'Сообщение для чата' })).toBeVisible()

    releaseInsights()
    await expect(page.locator('.chat-panel .chat-title strong')).toHaveText('Помощь по приложению')
    await expect(page.getByRole('textbox', { name: 'Сообщение для чата' })).toBeVisible()
    expect(await page.evaluate(() => localStorage.getItem('document-checker-selected-application-chat'))).toBe(appChat.id)
  } finally {
    releaseInsights?.()
    await page.unroute(insightsUrl)
    await request.delete(`/api/v1/chats/${appChat.id}`)
    await request.delete(`/api/v1/documents/${document.id}`)
  }
})

test('document chat keeps application and document retrieval separate for app-only, mixed and document-only turns', async ({ page, request }) => {
  await provider(request, { mode: 'ready', reset_counters: true })
  const document = await upload(page, 'sample.txt')
  let survivorChatId = null
  try {
  await showChat(page)
  const documentChat = await (await request.get(`/api/v1/documents/${document.id}/chat`)).json()
  const input = page.getByRole('textbox', { name: 'Сообщение для чата' })
  await expect(input).toBeEnabled()

  await input.fill('Как поменять модель?')
  await input.press('Enter')
  await expect(page.locator('.chat-message.assistant-message').last().locator('.message-text')).toContainText('Подтверждённая возможность приложения.')
  let state = await (await request.post('/api/v1/__e2e/provider', { data: { mode: 'ready' } })).json()
  expect(state.last_request_metadata).toMatchObject({ scope: 'application', document_evidence_count: 0, ephemeral: true })
  let messages = await (await request.get(`/api/v1/chats/${documentChat.id}/messages`)).json()
  expect(messages.at(-1).citations.every(source => source.source_type === 'application')).toBeTruthy()

  await input.fill('Кратко перескажи документ и где изменить модель?')
  await input.press('Enter')
  await expect(page.locator('.chat-message.assistant-message').last().locator('.message-text')).toContainText('Также найден фрагмент документа.')
  state = await (await request.post('/api/v1/__e2e/provider', { data: { mode: 'ready' } })).json()
  expect(state.last_request_metadata).toMatchObject({ scope: 'mixed', ephemeral: true })
  expect(state.last_request_metadata.application_evidence_count).toBeGreaterThan(0)
  expect(state.last_request_metadata.document_evidence_count).toBeGreaterThan(0)
  messages = await (await request.get(`/api/v1/chats/${documentChat.id}/messages`)).json()
  expect(new Set(messages.at(-1).citations.map(source => source.source_type))).toEqual(new Set(['application', 'document']))

  await input.fill('Кратко перескажи документ')
  await input.press('Enter')
  await expect(page.locator('.chat-message.assistant-message').last().locator('.message-text')).toContainText('Синтетический ответ')
  messages = await (await request.get(`/api/v1/chats/${documentChat.id}/messages`)).json()
  const documentOnlyAnswer = messages.filter(message => message.role === 'assistant').at(-1)
  expect(documentOnlyAnswer.content).toContain('Синтетический ответ')
  expect(documentOnlyAnswer.citations.length).toBeGreaterThan(0)
  expect(documentOnlyAnswer.citations.every(source => source.source_type === 'document')).toBeTruthy()

  expect((await request.delete(`/api/v1/chats/${documentChat.id}`)).status()).toBe(409)
  const disposableChat = await (await request.post('/api/v1/chats/application')).json()
  expect((await request.delete(`/api/v1/chats/${disposableChat.id}`)).ok()).toBeTruthy()
  expect((await request.get(`/api/v1/documents/${document.id}`)).status()).toBe(200)
  const survivor = await (await request.post('/api/v1/chats/application')).json()
  survivorChatId = survivor.id
  await request.delete(`/api/v1/documents/${document.id}`)
  expect((await request.get(`/api/v1/chats/${survivorChatId}`)).status()).toBe(200)
  await request.delete(`/api/v1/chats/${survivorChatId}`)
  survivorChatId = null
  } finally {
    await request.delete(`/api/v1/documents/${document.id}`)
    if (survivorChatId) await request.delete(`/api/v1/chats/${survivorChatId}`)
  }
})
