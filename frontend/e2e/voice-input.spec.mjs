import { test, expect, provider, upload, showChat } from './helpers.mjs'

test('Local dictation edits the draft only and stops safely on cancel, close, and errors', async ({ page, request }) => {
  test.setTimeout(180_000)
  await provider(request)
  const messagePosts = []
  page.on('request', browserRequest => {
    const url = new URL(browserRequest.url())
    if (browserRequest.method() === 'POST' && /^\/api\/v1\/chats\/[^/]+\/messages$/.test(url.pathname)) {
      messagePosts.push(url.pathname)
    }
  })
  await page.addInitScript(() => {
    const state = {
      availability: 'available',
      transcript: 'сроки',
      error: '',
      options: null,
      starts: 0,
      aborts: 0,
      lastInstance: null,
    }
    class MockSpeechRecognition {
      lang = ''
      continuous = false
      interimResults = false
      maxAlternatives = 1
      processLocally = false
      onstart = null
      onresult = null
      onerror = null
      onend = null

      static async available(options) {
        state.options = options
        return state.availability
      }

      start() {
        state.starts += 1
        state.lastInstance = this
        this.onstart?.(new Event('start'))
        if (state.error) {
          queueMicrotask(() => {
            this.onerror?.(Object.assign(new Event('error'), { error: state.error }))
            this.onend?.(new Event('end'))
          })
        }
      }

      stop() {
        const result = { isFinal: true, 0: { transcript: state.transcript } }
        this.onresult?.({ resultIndex: 0, results: [result] })
        this.onend?.(new Event('end'))
      }

      abort() {
        state.aborts += 1
      }
    }
    Object.defineProperty(window, 'SpeechRecognition', { value: MockSpeechRecognition, configurable: true })
    Object.defineProperty(window, '__voiceMock', { value: state, configurable: true })
  })

  await upload(page, 'sample.txt')
  await showChat(page)
  const composer = page.locator('.chat-panel .ai-composer')
  const input = composer.getByLabel('Сообщение для чата', { exact: true })
  const status = composer.locator('.ai-composer-status')
  const startButton = composer.getByRole('button', { name: 'Голосовой ввод', exact: true })

  await input.fill('Вопрос: документ')
  await input.evaluate(element => element.setSelectionRange(8, 8))
  await startButton.click()
  const stopButton = composer.getByRole('button', { name: 'Остановить диктовку', exact: true })
  await expect(stopButton).toBeVisible()
  await expect(status).toContainText('Идёт запись')
  const localConfiguration = await page.evaluate(() => ({
    options: window.__voiceMock.options,
    processLocally: window.__voiceMock.lastInstance.processLocally,
    language: window.__voiceMock.lastInstance.lang,
  }))
  expect(localConfiguration.options).toEqual({ langs: ['ru-RU'], processLocally: true, quality: 'dictation' })
  expect(localConfiguration.processLocally).toBe(true)
  expect(localConfiguration.language).toBe('ru-RU')

  await stopButton.click()
  await expect(input).toHaveValue('Вопрос: сроки документ')
  await expect(status).toContainText('Проверьте его перед отправкой')
  expect(messagePosts, 'Speech recognition must never send the chat message').toHaveLength(0)

  await page.evaluate(() => { window.__voiceMock.transcript = 'отменённый текст' })
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(composer.getByRole('button', { name: 'Остановить диктовку', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(status).toContainText('Диктовка отменена. Черновик не изменён.')
  await expect(input).toHaveValue('Вопрос: сроки документ')
  await expect.poll(() => page.evaluate(() => window.__voiceMock.aborts)).toBe(1)

  await page.evaluate(() => { window.__voiceMock.transcript = 'при закрытии' })
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(composer.getByRole('button', { name: 'Остановить диктовку', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Свернуть чат', exact: true }).click()
  await expect(page.locator('.chat-panel')).toBeHidden()
  await expect.poll(() => page.evaluate(() => window.__voiceMock.aborts)).toBe(2)
  await page.locator('.chat-visibility-toggle[aria-label="Открыть чат"]').click()
  await expect(input).toBeVisible()
  await expect(input).toHaveValue('Вопрос: сроки документ')

  await page.evaluate(() => { window.__voiceMock.error = 'not-allowed' })
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(status).toContainText('Нет доступа к микрофону')
  await expect(composer.getByRole('button', { name: 'Голосовой ввод', exact: true })).toBeEnabled()
  await expect(input).toHaveValue('Вопрос: сроки документ')

  await page.evaluate(() => {
    window.__voiceMock.error = ''
    window.__voiceMock.availability = 'downloadable'
  })
  const startsBeforeUnavailableLanguage = await page.evaluate(() => window.__voiceMock.starts)
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(status).toContainText('Автозагрузка отключена')
  expect(await page.evaluate(() => window.__voiceMock.starts)).toBe(startsBeforeUnavailableLanguage)
  await expect(input).toHaveValue('Вопрос: сроки документ')

  await page.evaluate(() => {
    window.__voiceMock.availability = 'available'
    // Removing an own override can expose Chromium's native prototype property again.
    Object.defineProperty(window, 'SpeechRecognition', { value: undefined, configurable: true })
    Object.defineProperty(window, 'webkitSpeechRecognition', { value: undefined, configurable: true })
  })
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(status).toContainText('не поддерживается этим браузером')

  await page.setViewportSize({ width: 390, height: 844 })
  await showChat(page)
  await expect(composer.getByRole('button', { name: 'Голосовой ввод', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Voice status does not create mobile horizontal overflow').toBeTruthy()
  expect(messagePosts, 'Transcript is not submitted without an explicit send action').toHaveLength(0)
})
