import { test, expect, upload, provider } from './helpers.mjs'

test('chat library pages, searches old messages, renames and pins durably', async ({ page, request }) => {
  await provider(request)
  const document = await upload(page, 'sample.txt')
  const chats = await (await request.get('/api/v1/chats')).json()
  const originalChat = chats.find(item => item.document_id === document.id)
  expect(originalChat, 'Uploaded document has a persistent chat').toBeTruthy()
  const beforeSeed = await (await request.get('/api/v1/chats/library?limit=1')).json()
  const marker = `M11_ARCHIVED_MESSAGE_${Date.now()}`
  const seededResponse = await request.post('/api/v1/__e2e/library/seed', {
    data: { target_document_id: document.id, count: 31, marker },
  })
  expect(seededResponse.ok(), await seededResponse.text()).toBeTruthy()
  const seeded = await seededResponse.json()

  try {
    await page.reload()
    await expect(page.locator('.document-row')).toHaveCount(30)
    await expect(page.getByRole('button', { name: /Показать ещё чаты/ })).toBeVisible()
    await page.getByRole('button', { name: /Показать ещё чаты/ }).click()
    const expectedTotal = beforeSeed.total + seeded.document_ids.length
    await expect(page.locator('.document-row')).toHaveCount(Math.min(60, expectedTotal))
    await expect(page.getByRole('button', { name: /Показать ещё чаты/ })).toHaveCount(expectedTotal > 60 ? 1 : 0)

    const search = page.getByRole('searchbox', { name: 'Поиск по чатам' })
    await search.fill(marker)
    const archivedRow = page.locator(`[data-chat-id="${originalChat.id}"]`)
    await expect(archivedRow).toBeVisible()
    await expect(archivedRow.locator('.chat-search-snippet')).toContainText(marker)
    await archivedRow.locator('.document-select').click()
    const matchedMessage = page.locator(`[data-message-id="${seeded.target_message_id}"]`)
    await expect(matchedMessage).toHaveClass(/search-result-highlight/)
    await expect(matchedMessage).toContainText(marker)

    await search.fill('sample.txt')
    await expect(archivedRow).toBeVisible()
    await archivedRow.locator('.row-actions-trigger').click()
    await page.getByRole('menuitem', { name: 'Закрепить чат', exact: true }).click()
    await expect(archivedRow.locator('.chat-pinned-indicator')).toBeVisible()

    await archivedRow.locator('.row-actions-trigger').click()
    await page.getByRole('menuitem', { name: 'Переименовать', exact: true }).click()
    const renameInput = page.getByRole('textbox', { name: 'Название чата' })
    await expect(renameInput).toBeFocused()
    await renameInput.fill('Архивный анализ M11')
    await page.getByRole('button', { name: 'Сохранить название чата' }).click()
    const renamedRow = page.locator(`[data-chat-id="${originalChat.id}"]`)
    await expect(renamedRow.getByRole('button', { name: 'Открыть чат Архивный анализ M11' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'sample.txt', exact: true })).toBeVisible()

    // A competing tab wins the revision; the inline editor must retain the draft and explain the conflict.
    await renamedRow.locator('.row-actions-trigger').click()
    await page.getByRole('menuitem', { name: 'Переименовать', exact: true }).click()
    await page.getByRole('textbox', { name: 'Название чата' }).fill('Локальное изменение')
    const settings = await (await request.get(`/api/v1/chats/${originalChat.id}`)).json()
    const concurrent = await request.patch(`/api/v1/chats/${originalChat.id}`, {
      data: { expected_revision: settings.revision, title: 'Название из другой вкладки' },
    })
    expect(concurrent.ok(), await concurrent.text()).toBeTruthy()
    await page.getByRole('button', { name: 'Сохранить название чата' }).click()
    await expect(page.getByRole('alert')).toContainText('изменился в другой вкладке')
    await expect(page.getByRole('textbox', { name: 'Название чата' })).toHaveValue('Локальное изменение')
    await page.keyboard.press('Escape')

    const durable = await (await request.get(`/api/v1/chats/${originalChat.id}`)).json()
    expect(durable.title).toBe('Название из другой вкладки')
    expect(durable.pinned).toBe(true)
    const summary = (await (await request.get('/api/v1/chats')).json()).find(item => item.id === originalChat.id)
    expect(summary.filename).toBe('sample.txt')
    expect(summary.title).toBe('Название из другой вкладки')

    await search.fill('')
    await expect.poll(() => page.locator('.document-row').first().getAttribute('data-chat-id')).toBe(originalChat.id)
    await expect(page.locator('.document-row').first()).toContainText('Название из другой вкладки')

    await page.getByRole('button', { name: 'Свернуть библиотеку' }).click()
    await expect.poll(() => page.locator('#chat-library').evaluate(element => Math.round(element.getBoundingClientRect().width))).toBe(72)
    const actions = page.locator(`[data-chat-id="${originalChat.id}"]`).getByRole('button', { name: 'Действия: Название из другой вкладки' })
    await expect(actions).toBeVisible()
    await actions.click()
    await expect(page.getByRole('menuitem', { name: 'Открепить чат', exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(actions).toBeFocused()

    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('button', { name: 'Открыть документы', exact: true }).click()
    await expect.poll(() => page.locator('#chat-library').evaluate(element => Math.round(element.getBoundingClientRect().width))).toBe(72)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  } finally {
    await request.delete('/api/v1/__e2e/library/seed', { data: { document_ids: seeded.document_ids } })
    await request.delete(`/api/v1/documents/${document.id}`)
  }
})
