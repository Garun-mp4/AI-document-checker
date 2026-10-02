import { test, expect, upload, provider, showChat } from './helpers.mjs'

test('context boundaries, durable message actions and versioned reanalysis', async ({ page, request }) => {
  await provider(request)
  const document = await upload(page, 'sample.txt')
  const chat = await (await request.get(`/api/v1/documents/${document.id}/chat`)).json()

  const firstResponse = await request.post(`/api/v1/chats/${chat.id}/messages`, { data: { text: 'Первый вопрос M12' } })
  expect(firstResponse.ok(), await firstResponse.text()).toBeTruthy()
  const firstMessages = await (await request.get(`/api/v1/chats/${chat.id}/messages`)).json()
  expect(firstMessages).toHaveLength(2)
  expect(firstMessages[1].generation_status).toBe('complete')
  expect(firstMessages[1].reply_to_message_id).toBe(firstMessages[0].id)
  expect(firstMessages[1].model).toBeTruthy()
  expect(firstMessages[1].source_version).toBeGreaterThan(0)

  const boundaryResponse = await request.post(`/api/v1/chats/${chat.id}/context`)
  expect(boundaryResponse.ok(), await boundaryResponse.text()).toBeTruthy()
  expect(await boundaryResponse.json()).toMatchObject({ context_epoch: 1, preserved_message_count: 2 })
  const secondResponse = await request.post(`/api/v1/chats/${chat.id}/messages`, { data: { text: 'Начать с чистого контекста' } })
  expect(secondResponse.ok(), await secondResponse.text()).toBeTruthy()
  const afterBoundary = await (await request.get(`/api/v1/chats/${chat.id}/messages`)).json()
  expect(afterBoundary).toHaveLength(4)
  expect(afterBoundary[2].context_epoch).toBe(1)
  expect(afterBoundary[3].context_epoch).toBe(1)
  expect(afterBoundary[3].content).toContain('История: 0 сообщений.')

  const deleted = await request.delete(`/api/v1/chats/${chat.id}/messages/${firstMessages[1].id}`)
  expect(deleted.ok(), await deleted.text()).toBeTruthy()
  expect((await deleted.json()).deleted_ids).toEqual(expect.arrayContaining([firstMessages[0].id, firstMessages[1].id]))
  const documentAfterDelete = await (await request.get(`/api/v1/documents/${document.id}`)).json()
  expect(documentAfterDelete.status).toBe('ready')
  expect(await (await request.get(`/api/v1/chats/${chat.id}/messages`)).json()).toHaveLength(2)

  const originalVersions = await (await request.get(`/api/v1/documents/${document.id}/versions`)).json()
  const originalActive = originalVersions.find(version => version.is_active)
  await page.reload()
  await showChat(page)
  const firstAssistant = page.locator('.assistant-message').first()
  await firstAssistant.getByRole('button', { name: 'Удалить сообщение и связанные ответы' }).click()
  await expect(page.getByRole('dialog')).toContainText('Начать с чистого контекста')
  await page.getByRole('button', { name: 'Отмена' }).click()

  const invalidChoice = await request.post(`/api/v1/documents/${document.id}/analysis/rebuild`, {
    data: { model: 'gpt-5.5', reasoning_effort: 'high', expected_source_version: originalActive.source_version },
  })
  expect(invalidChoice.status()).toBe(422)

  await provider(request, { hold_complete: true })
  let queued
  try {
    await page.getByLabel('Модель повторного анализа').selectOption('gpt-6.1-sol')
    await page.getByLabel('Уровень размышления повторного анализа').selectOption('high')
    await page.getByRole('button', { name: 'Повторить анализ' }).click()
    await expect(page.locator('.toast-message')).toContainText('Текущие карточки останутся доступны')
    await expect.poll(async () => {
      const jobs = await (await request.get(`/api/v1/documents/${document.id}/jobs`)).json()
      return jobs.find(job => job.operation === 'analysis' && job.version > originalActive.number)?.state
    }, { timeout: 30_000 }).toBe('running')
    expect(await page.locator('.insight-card').count()).toBe(7)
    const oldInsights = await (await request.get(`/api/v1/documents/${document.id}/insights?version=${originalActive.number}`)).json()
    await expect(page.locator('.insight-card').first()).toContainText(oldInsights[0].answer.slice(0, 60))
    queued = (await (await request.get(`/api/v1/documents/${document.id}/jobs`)).json())
      .find(job => job.operation === 'analysis' && job.version > originalActive.number)
    await provider(request)

    await expect.poll(async () => {
      const versions = await (await request.get(`/api/v1/documents/${document.id}/versions`)).json()
      return versions.find(version => version.is_active)?.number
    }, { timeout: 90_000 }).toBe(queued.version)
  } finally {
    await provider(request)
  }
  const versions = await (await request.get(`/api/v1/documents/${document.id}/versions`)).json()
  const newActive = versions.find(version => version.is_active)
  expect(newActive).toMatchObject({
    number: queued.version,
    source_version: originalActive.source_version,
    model: 'gpt-6.1-sol',
    reasoning_effort: 'high',
  })
  expect(versions.some(version => version.number === originalActive.number)).toBeTruthy()
  expect(await (await request.get(`/api/v1/documents/${document.id}/insights?version=${originalActive.number}`)).json()).toHaveLength(7)
  expect(await (await request.get(`/api/v1/documents/${document.id}/insights?version=${queued.version}`)).json()).toHaveLength(7)
  const jobs = await (await request.get(`/api/v1/documents/${document.id}/jobs`)).json()
  const analysisJob = jobs.find(job => job.version === queued.version)
  expect(analysisJob.operation).toBe('analysis')
  expect(analysisJob.parameters).toMatchObject({ model: 'gpt-6.1-sol', reasoning_effort: 'high' })
  const workerControl = await (await request.get('/api/v1/__e2e/worker-control')).json()
  expect(workerControl.last_complete_preferences).toMatchObject({ model: 'gpt-6.1-sol', reasoning_effort: 'high' })

  await page.reload()
  await showChat(page)
  await expect(page.locator('.chat-message')).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Начать новый контекст' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Версия анализа' })).toHaveCount(1)
  await expect(page.getByRole('combobox', { name: 'Модель повторного анализа' })).toHaveValue('gpt-6-luna')
  await page.getByRole('combobox', { name: 'Версия анализа' }).selectOption(String(originalActive.number))
  await expect(page.locator('.insight-card')).toHaveCount(7)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('.analysis-rebuild-panel')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})

test('stop persists a partial answer and retry reuses the original question', async ({ page, request }) => {
  await provider(request)
  const document = await upload(page, 'sample.txt')
  await provider(request, { hold_stream: true })
  await showChat(page)

  const input = page.getByRole('textbox', { name: 'Сообщение для чата' })
  await input.fill('Вопрос с остановкой')
  await input.press('Enter')
  await expect(page.locator('.chat-message.user-message')).toContainText('Вопрос с остановкой')
  await expect(page.getByRole('button', { name: 'Остановить' })).toBeVisible()
  await page.getByRole('button', { name: 'Остановить' }).click()
  const interrupted = page.locator('.message-interrupted-status')
  await expect(interrupted).toBeVisible()
  await expect(page.getByRole('button', { name: 'Повторить вопрос' })).toBeVisible()

  const chat = await (await request.get(`/api/v1/documents/${document.id}/chat`)).json()
  const interruptedMessages = await (await request.get(`/api/v1/chats/${chat.id}/messages`)).json()
  const question = interruptedMessages.find(message => message.role === 'user' && message.content === 'Вопрос с остановкой')
  const partial = interruptedMessages.find(message => message.role === 'assistant' && message.generation_status === 'interrupted')
  expect(question).toBeTruthy()
  expect(partial.reply_to_message_id).toBe(question.id)
  expect(partial.content).toContain('Синтетический ответ')

  await provider(request)
  await page.getByRole('button', { name: 'Повторить вопрос' }).click()
  await expect.poll(async () => {
    const rows = await (await request.get(`/api/v1/chats/${chat.id}/messages`)).json()
    return rows.filter(message => message.role === 'assistant' && message.reply_to_message_id === question.id && message.generation_status === 'complete').length
  }, { timeout: 30_000 }).toBe(1)
  const retried = await (await request.get(`/api/v1/chats/${chat.id}/messages`)).json()
  expect(retried.filter(message => message.role === 'user' && message.content === 'Вопрос с остановкой')).toHaveLength(1)
  expect(retried.filter(message => message.role === 'assistant' && message.reply_to_message_id === question.id)).toHaveLength(2)
})
