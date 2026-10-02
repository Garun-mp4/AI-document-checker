import { test, expect, provider, upload, showChat } from './helpers.mjs'

async function createApplicationChat(page, request, targetId) {
  await provider(request, { mode: 'ready', ui_target_id: targetId })
  await page.goto('/')
  await page.getByRole('button', { name: 'Новый чат', exact: true }).click()
  const createdRequest = page.waitForResponse(response => response.url().endsWith('/api/v1/chats/application') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Спросить о работе приложения' }).click()
  return (await createdRequest).json()
}

async function ask(page, chatId, question, targetLabel) {
  const input = page.getByRole('textbox', { name: 'Сообщение для чата' })
  await input.fill(question)
  const sent = page.waitForResponse(response => response.url().endsWith(`/api/v1/chats/${chatId}/messages`) && response.request().method() === 'POST')
  await input.press('Enter')
  await sent
  const message = page.locator('.chat-message.assistant-message').last()
  const button = message.getByRole('button', { name: `Показать в интерфейсе: ${targetLabel}` })
  await expect(button).toBeVisible()
  return { message, button }
}

test('validated UI target persists, highlights only after keyboard or pointer activation, and never runs the control', async ({ page, request }) => {
  const chat = await createApplicationChat(page, request, 'codex.settings.open')
  try {
    const { message, button } = await ask(page, chat.id, 'Где изменить модель?', 'Подключение Codex')
    const persisted = (await (await request.get(`/api/v1/chats/${chat.id}/messages`)).json()).at(-1)
    const apiBuild = await (await request.get('/api/v1/version')).json()
    expect(persisted).toMatchObject({
      ui_target_id: 'codex.settings.open',
      ui_target_catalog_version: '1',
      ui_target_build_id: apiBuild.build_id,
    })

    await page.reload()
    const restoredMessage = page.locator('.chat-message.assistant-message').last()
    const restoredButton = restoredMessage.getByRole('button', { name: 'Показать в интерфейсе: Подключение Codex' })
    await expect(restoredButton).toBeVisible()
    await expect(restoredMessage.locator('.message-text')).toContainText('Подтверждённая возможность приложения.')

    const target = page.locator('[data-help-target="codex.settings.open"]')
    let fileChooserCount = 0
    page.on('filechooser', () => { fileChooserCount += 1 })
    await restoredButton.focus()
    await restoredButton.press('Enter')
    await expect(target).toHaveAttribute('data-help-highlighted', 'true')
    await expect(restoredMessage.getByRole('status')).toContainText('Фокус не перемещён')
    expect(await restoredButton.evaluate(element => element === document.activeElement)).toBeTruthy()
    await expect(page.locator('.modal-backdrop')).toHaveCount(0)
    expect(fileChooserCount).toBe(0)

    const sizes = [
      { width: 1440, height: 900 }, { width: 1280, height: 720 },
      { width: 1024, height: 768 }, { width: 768, height: 1024 },
      { width: 430, height: 932 }, { width: 390, height: 844 },
    ]
    for (const size of sizes) {
      await page.setViewportSize(size)
      const openChatButton = page.getByRole('button', { name: 'Открыть чат', exact: true })
      if (await openChatButton.count()) await openChatButton.click()
      await expect(restoredButton).toBeVisible()
      await restoredButton.click()
      await expect(restoredMessage.getByRole('status')).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `No horizontal page overflow at ${size.width}x${size.height}`).toBeTruthy()
      await target.evaluate(element => element.removeAttribute('data-help-highlighted'))
    }

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await target.evaluate(element => {
      const nativeScrollIntoView = element.scrollIntoView.bind(element)
      element.scrollIntoView = options => {
        element.dataset.e2eScrollBehavior = options?.behavior || 'auto'
        nativeScrollIntoView(options)
      }
    })
    await restoredButton.press('Enter')
    await expect(target).toHaveAttribute('data-help-highlighted', 'true')
    expect(await target.evaluate(element => getComputedStyle(element).transitionDuration)).toBe('0s')

    await page.waitForTimeout(1_000)
    await restoredButton.click()
    await page.waitForTimeout(1_000)
    await expect(target).toHaveAttribute('data-help-highlighted', 'true')
    await page.waitForTimeout(950)
    await expect(target).not.toHaveAttribute('data-help-highlighted', 'true')
    await expect(page.locator('.modal-backdrop')).toHaveCount(0)
  } finally {
    await request.delete(`/api/v1/chats/${chat.id}`)
  }
})

test('unavailable, disabled, covered, ambiguous and stale targets use a text fallback', async ({ page, request }) => {
  const chat = await createApplicationChat(page, request, 'codex.settings.open')
  try {
    const { message, button } = await ask(page, chat.id, 'Где изменить модель?', 'Подключение Codex')
    const target = page.locator('[data-help-target="codex.settings.open"]')

    await target.evaluate(element => { element.hidden = true })
    await button.click()
    await expect(message.getByRole('status')).toContainText('скрыт')
    await expect(target).not.toHaveAttribute('data-help-highlighted', 'true')
    await target.evaluate(element => { element.hidden = false; element.setAttribute('disabled', 'true') })
    await button.click()
    await expect(message.getByRole('status')).toContainText('отключён')
    await target.evaluate(element => { element.removeAttribute('disabled'); element.setAttribute('aria-busy', 'true') })
    await button.click()
    await expect(message.getByRole('status')).toContainText('загрузкой')
    await target.evaluate(element => element.removeAttribute('aria-busy'))

    await target.evaluate(element => {
      const rect = element.getBoundingClientRect()
      const cover = document.createElement('div')
      cover.dataset.e2eCover = 'true'
      Object.assign(cover.style, {
        position: 'fixed', left: `${rect.left}px`, top: `${rect.top}px`,
        width: `${rect.width}px`, height: `${rect.height}px`, zIndex: '99999', background: 'transparent',
      })
      document.body.append(cover)
    })
    await button.click()
    await expect(message.getByRole('status')).toContainText('перекрыт')
    await page.locator('[data-e2e-cover="true"]').evaluate(element => element.remove())

    await provider(request, { mode: 'ready', ui_target_id: 'chat-library.toggle' })
    const input = page.getByRole('textbox', { name: 'Сообщение для чата' })
    await input.fill('Как открыть библиотеку чатов?')
    const secondResponse = page.waitForResponse(response => response.url().endsWith(`/api/v1/chats/${chat.id}/messages`) && response.request().method() === 'POST')
    await input.press('Enter')
    await secondResponse
    const secondMessage = page.locator('.chat-message.assistant-message').last()
    const secondButton = secondMessage.getByRole('button', { name: 'Показать в интерфейсе: Библиотека чатов' })
    await expect(secondButton).toBeVisible()
    const mobileDuplicate = page.locator('.mobile-menu')
    await mobileDuplicate.evaluate(element => {
      element.style.setProperty('display', 'grid', 'important')
      element.style.setProperty('position', 'fixed', 'important')
      element.style.setProperty('left', '20px', 'important')
      element.style.setProperty('top', '180px', 'important')
      element.style.setProperty('width', '44px', 'important')
      element.style.setProperty('height', '44px', 'important')
      element.style.setProperty('z-index', '9999', 'important')
      element.style.setProperty('opacity', '1', 'important')
      element.style.setProperty('visibility', 'visible', 'important')
    })
    await secondButton.click()
    await expect(secondMessage.getByRole('status')).toContainText('несколько одинаковых')
    await expect(page.locator('[data-help-target="chat-library.toggle"][data-help-highlighted="true"]')).toHaveCount(0)

    await page.unrouteAll({ behavior: 'wait' })
    const messagesRoute = `**/api/v1/chats/${chat.id}/messages`
    await page.route(messagesRoute, async route => {
      const response = await route.fetch()
      const rows = await response.json()
      for (const row of rows) {
        if (row.ui_target_id) {
          row.ui_target_catalog_version = '0'
          row.ui_target_build_id = 'previous-build'
        }
      }
      await route.fulfill({ response, json: rows })
    })
    await page.reload()
    const latestMessage = page.locator('.chat-message.assistant-message').last()
    await expect(latestMessage.locator('.message-text')).toContainText('Подтверждённая возможность приложения.')
    await expect(latestMessage.getByRole('button', { name: /Показать в интерфейсе/ })).toHaveCount(0)
  } finally {
    await request.delete(`/api/v1/chats/${chat.id}`)
  }
})

test('an offscreen document control scrolls into view while respecting reduced motion', async ({ page, request }) => {
  const uploadedDocument = await upload(page, 'sample.txt')
  try {
    await provider(request, { mode: 'ready', ui_target_id: 'workspace.layout.select' })
    await showChat(page)
    const chat = await (await request.get(`/api/v1/documents/${uploadedDocument.id}/chat`)).json()
    const { message, button } = await ask(page, chat.id, 'Как выбрать расположение документа сверху или слева?', 'Расположение документа')
    const workspaceScroll = page.locator('.workspace')
    await expect.poll(() => workspaceScroll.evaluate(element => element.scrollHeight - element.clientHeight)).toBeGreaterThan(40)
    await workspaceScroll.evaluate(element => { element.scrollTop = element.scrollHeight })
    const target = page.locator('[data-help-target="workspace.layout.select"]')
    await expect.poll(() => target.evaluate(element => element.getBoundingClientRect().top)).toBeLessThan(await workspaceScroll.evaluate(element => element.getBoundingClientRect().top))

    await page.emulateMedia({ reducedMotion: 'reduce' })
    await target.evaluate(element => {
      const nativeScrollIntoView = element.scrollIntoView.bind(element)
      element.scrollIntoView = options => {
        element.dataset.e2eScrollBehavior = options?.behavior || 'auto'
        nativeScrollIntoView(options)
      }
    })
    await button.focus()
    await button.press('Enter')
    await expect(target).toHaveAttribute('data-help-highlighted', 'true')
    expect(await target.getAttribute('data-e2e-scroll-behavior')).toBe('instant')
    expect(await target.evaluate(element => element.getBoundingClientRect().top)).toBeGreaterThanOrEqual(await workspaceScroll.evaluate(element => element.getBoundingClientRect().top))
    await expect(message.getByRole('status')).toContainText('Фокус не перемещён')
    expect(await button.evaluate(element => element === window.document.activeElement)).toBeTruthy()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  } finally {
    await request.delete(`/api/v1/documents/${uploadedDocument.id}`)
  }
})
