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
      browserAvailability: 'available',
      transcript: 'сроки',
      error: '',
      options: null,
      availabilityChecks: [],
      installOptions: null,
      installCalls: 0,
      installResult: true,
      starts: 0,
      aborts: 0,
      lastInstance: null,
      microphoneConstraints: [],
      microphoneError: false,
      microphonePending: false,
      resolvePendingMicrophone: null,
      microphoneAmplitude: 32,
      microphoneSampleCount: 0,
      tracksStopped: 0,
      contextsClosed: 0,
      audioConnected: false,
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
        state.availabilityChecks.push(options)
        return options.processLocally ? state.availability : state.browserAvailability
      }

      static async install(options) {
        state.installOptions = options
        state.installCalls += 1
        if (state.installResult) state.availability = 'available'
        return state.installResult
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
    const mediaDevices = navigator.mediaDevices ?? {}
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        ...mediaDevices,
        getUserMedia: async constraints => {
          state.microphoneConstraints.push(constraints)
          if (state.microphonePending) {
            return new Promise(resolve => {
              state.resolvePendingMicrophone = () => resolve({ getTracks: () => [{ stop: () => { state.tracksStopped += 1 } }] })
            })
          }
          if (state.microphoneError) throw new DOMException('Microphone permission denied', 'NotAllowedError')
          return { getTracks: () => [{ stop: () => { state.tracksStopped += 1 } }] }
        },
      },
    })
    class MockAudioContext {
      state = 'suspended'

      createMediaStreamSource() {
        return {
          connect: () => { state.audioConnected = true },
          disconnect: () => { state.audioConnected = false },
        }
      }

      createAnalyser() {
        return {
          fftSize: 512,
          smoothingTimeConstant: 0,
          getByteTimeDomainData(samples) {
            state.microphoneSampleCount += 1
            const amplitude = Math.round(state.microphoneAmplitude * (0.15 + 0.85 * Math.abs(Math.sin(state.microphoneSampleCount * 0.71))))
            for (let index = 0; index < samples.length; index += 1) {
              samples[index] = index % 2 === 0 ? 128 - amplitude : 128 + amplitude
            }
          },
          disconnect() {},
        }
      }

      async resume() { this.state = 'running' }
      async close() { state.contextsClosed += 1; this.state = 'closed' }
    }
    Object.defineProperty(window, 'AudioContext', { value: MockAudioContext, configurable: true })
    Object.defineProperty(window, '__voiceMock', { value: state, configurable: true })
  })

  await upload(page, 'sample.txt')
  await showChat(page)
  const composer = page.locator('.chat-panel .ai-composer')
  const input = composer.getByLabel('Сообщение для чата', { exact: true })
  const status = composer.locator('.ai-composer-status')
  const recordingStatus = composer.locator('.ai-composer-recording-status')
  const startButton = composer.getByRole('button', { name: 'Голосовой ввод', exact: true })

  await input.fill('Вопрос: документ')
  await input.evaluate(element => element.setSelectionRange(8, 8))
  expect(await page.evaluate(() => window.__voiceMock.microphoneConstraints), 'Microphone capture must wait for an explicit user action').toHaveLength(0)
  await startButton.click()
  const stopButton = composer.getByRole('button', { name: 'Завершить диктовку', exact: true })
  await expect(stopButton).toBeVisible()
  await expect(stopButton).toBeFocused()
  await expect(recordingStatus).toContainText('шкала показывает уровень микрофона')
  await expect(composer.locator('.voice-level-meter-bar')).toHaveCount(29)
  const microphoneSetup = await page.evaluate(() => ({
    constraints: window.__voiceMock.microphoneConstraints,
    audioConnected: window.__voiceMock.audioConnected,
  }))
  expect(microphoneSetup.constraints).toEqual([{ audio: true, video: false }])
  expect(microphoneSetup.audioConnected).toBe(true)

  const getMaxMeterScale = () => composer.locator('.voice-level-meter-bar').evaluateAll(bars => Math.max(...bars.map(bar => Number(bar.style.transform.match(/scaleY\(([^)]+)\)/)?.[1] ?? 0))))
  await page.evaluate(() => { window.__voiceMock.microphoneAmplitude = 8 })
  await expect.poll(getMaxMeterScale, { timeout: 10_000 }).toBeLessThan(0.9)
  const quietMeterScale = await getMaxMeterScale()
  await page.evaluate(() => { window.__voiceMock.microphoneAmplitude = 72 })
  await expect.poll(getMaxMeterScale).toBeGreaterThan(quietMeterScale)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  expect(await composer.locator('.ai-composer-recording').evaluate(element => getComputedStyle(element).animationName)).toBe('none')
  const getMeterScaleCount = () => composer.locator('.voice-level-meter-bar').evaluateAll(bars => new Set(bars.map(bar => bar.style.transform)).size)
  await expect.poll(getMeterScaleCount).toBe(1)

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
  await expect(input).toBeFocused()
  await expect.poll(() => input.evaluate(element => [element.selectionStart, element.selectionEnd])).toEqual([14, 14])
  await expect.poll(() => page.evaluate(() => window.__voiceMock.tracksStopped)).toBe(1)
  await expect.poll(() => page.evaluate(() => window.__voiceMock.contextsClosed)).toBe(1)
  expect(messagePosts, 'Speech recognition must never send the chat message').toHaveLength(0)

  await page.evaluate(() => { window.__voiceMock.transcript = 'отменённый кнопкой текст' })
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(composer.getByRole('button', { name: 'Завершить диктовку', exact: true })).toBeVisible()
  await composer.getByRole('button', { name: 'Отменить диктовку', exact: true }).click()
  await expect(status).toContainText('Диктовка отменена. Черновик не изменён.')
  await expect(input).toHaveValue('Вопрос: сроки документ')
  await expect.poll(() => input.evaluate(element => [element.selectionStart, element.selectionEnd])).toEqual([14, 14])
  await expect.poll(() => page.evaluate(() => window.__voiceMock.aborts)).toBe(1)
  await expect.poll(() => page.evaluate(() => window.__voiceMock.tracksStopped)).toBe(2)

  await page.evaluate(() => { window.__voiceMock.transcript = 'отменённый клавишей текст' })
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(composer.getByRole('button', { name: 'Завершить диктовку', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(status).toContainText('Диктовка отменена. Черновик не изменён.')
  await expect(input).toHaveValue('Вопрос: сроки документ')
  await expect.poll(() => input.evaluate(element => [element.selectionStart, element.selectionEnd])).toEqual([14, 14])
  await expect.poll(() => page.evaluate(() => window.__voiceMock.aborts)).toBe(2)

  await page.evaluate(() => { window.__voiceMock.transcript = 'при закрытии' })
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(composer.getByRole('button', { name: 'Завершить диктовку', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Свернуть чат', exact: true }).click()
  await expect(page.locator('.chat-panel')).toBeHidden()
  await expect.poll(() => page.evaluate(() => window.__voiceMock.aborts)).toBe(3)
  await page.locator('.chat-visibility-toggle[aria-label="Открыть чат"]').click()
  await expect(input).toBeVisible()
  await expect(input).toHaveValue('Вопрос: сроки документ')
  await expect.poll(() => input.evaluate(element => [element.selectionStart, element.selectionEnd])).toEqual([14, 14])
  await expect.poll(() => page.evaluate(() => window.__voiceMock.tracksStopped)).toBe(4)

  const startsBeforePendingCancel = await page.evaluate(() => window.__voiceMock.starts)
  await page.evaluate(() => { window.__voiceMock.microphonePending = true })
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  const cancelRequestButton = composer.getByRole('button', { name: 'Отменить запрос микрофона', exact: true })
  await expect(cancelRequestButton).toBeVisible()
  await cancelRequestButton.click()
  await expect(status).toContainText('Диктовка отменена')
  await page.evaluate(() => {
    window.__voiceMock.microphonePending = false
    window.__voiceMock.resolvePendingMicrophone?.()
  })
  await expect.poll(() => page.evaluate(() => window.__voiceMock.tracksStopped)).toBe(5)
  await expect.poll(() => page.evaluate(() => window.__voiceMock.contextsClosed)).toBe(5)
  expect(await page.evaluate(() => window.__voiceMock.starts)).toBe(startsBeforePendingCancel)
  await expect.poll(() => input.evaluate(element => [element.selectionStart, element.selectionEnd])).toEqual([14, 14])

  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 1280, height: 720 },
    { width: 1024, height: 768 },
    { width: 768, height: 1024 },
    { width: 430, height: 932 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport)
    const chatToggle = page.locator('.chat-visibility-toggle')
    if (await chatToggle.getAttribute('aria-expanded') === 'false') await chatToggle.click()
    await expect(composer).toBeVisible()
    const originalSelection = await input.evaluate(element => [element.selectionStart, element.selectionEnd])
    await startButton.click()
    const responsiveStop = composer.getByRole('button', { name: 'Завершить диктовку', exact: true })
    await expect(responsiveStop).toBeVisible()
    await expect(composer.locator('.voice-level-meter-bar')).toHaveCount(29)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `Recording panel fits ${viewport.width}x${viewport.height}`).toBeTruthy()
    await composer.getByRole('button', { name: 'Отменить диктовку', exact: true }).click()
    await expect(startButton).toBeVisible()
    await expect.poll(() => input.evaluate(element => [element.selectionStart, element.selectionEnd])).toEqual(originalSelection)
  }

  await page.evaluate(() => {
    window.__voiceMock.microphoneError = true
    window.__voiceMock.transcript = 'текст без измерителя'
  })
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  const fallbackStopButton = composer.getByRole('button', { name: 'Завершить диктовку', exact: true })
  await expect(fallbackStopButton).toBeVisible()
  await expect(recordingStatus).toContainText('индикатор уровня недоступен')
  await expect(fallbackStopButton).toBeEnabled()
  await fallbackStopButton.click()
  await expect(input).toHaveValue('Вопрос: сроки текст без измерителя документ')
  await input.fill('Вопрос: сроки документ')
  await page.evaluate(() => { window.__voiceMock.microphoneError = false })

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
  const microphoneRequestsBeforePackageInstall = await page.evaluate(() => window.__voiceMock.microphoneConstraints.length)
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(status).toContainText('установить вручную')
  const installLocalPackButton = composer.getByRole('button', { name: 'Загрузить локальный пакет', exact: true })
  await expect(installLocalPackButton).toBeVisible()
  expect(await page.evaluate(() => window.__voiceMock.starts)).toBe(startsBeforeUnavailableLanguage)
  expect(await page.evaluate(() => window.__voiceMock.microphoneConstraints)).toHaveLength(13)
  await installLocalPackButton.click()
  await expect(status).toContainText('пакет установлен')
  expect(await page.evaluate(() => window.__voiceMock.installOptions)).toEqual({ langs: ['ru-RU'], processLocally: true, quality: 'dictation' })
  expect(await page.evaluate(() => window.__voiceMock.installCalls)).toBe(1)
  expect(await page.evaluate(() => window.__voiceMock.starts)).toBe(startsBeforeUnavailableLanguage)
  expect(await page.evaluate(() => window.__voiceMock.microphoneConstraints)).toHaveLength(microphoneRequestsBeforePackageInstall)
  await startButton.click()
  await expect(composer.getByRole('button', { name: 'Завершить диктовку', exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.__voiceMock.lastInstance.processLocally)).toBe(true)
  await composer.getByRole('button', { name: 'Завершить диктовку', exact: true }).click()
  await input.fill('Вопрос: сроки документ')
  await expect(input).toHaveValue('Вопрос: сроки документ')

  await page.evaluate(() => {
    window.__voiceMock.availability = 'available'
    // Removing an own override can expose Chromium's native prototype property again.
    Object.defineProperty(window, 'SpeechRecognition', { value: undefined, configurable: true })
    Object.defineProperty(window, 'webkitSpeechRecognition', { value: undefined, configurable: true })
  })
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(status).toContainText('не поддерживается этим браузером')

  await page.evaluate(() => {
    const MockSpeechRecognition = window.__voiceMock.lastInstance.constructor
    Object.defineProperty(window, 'SpeechRecognition', { value: MockSpeechRecognition, configurable: true })
    window.__voiceMock.availability = 'unavailable'
    window.__voiceMock.transcript = 'сроки'
  })
  const startsBeforeBrowserConsent = await page.evaluate(() => window.__voiceMock.starts)
  const microphoneRequestsBeforeBrowserConsent = await page.evaluate(() => window.__voiceMock.microphoneConstraints.length)
  await composer.getByRole('button', { name: 'Голосовой ввод', exact: true }).click()
  await expect(status).toContainText('Локальная русская диктовка недоступна')
  const browserRecognitionOption = composer.getByRole('button', { name: 'Распознать браузером…', exact: true })
  await expect(browserRecognitionOption).toBeVisible()
  const browserConsent = page.getByRole('dialog', { name: 'Распознавать речь браузером?' })
  await expect(browserConsent, 'Unavailable on-device Russian recognition immediately offers a clear, explicit browser-mode choice').toBeVisible()
  await expect(browserConsent).toContainText('браузер может передавать аудио внешнему сервису')
  await expect(browserConsent).toContainText('не отправится в чат')
  await expect(browserConsent.getByRole('button', { name: 'Отмена', exact: true })).toBeFocused()
  expect(await page.evaluate(() => window.__voiceMock.starts), 'Opening consent never starts browser recognition').toBe(startsBeforeBrowserConsent)
  expect(await page.evaluate(() => window.__voiceMock.microphoneConstraints.length), 'Opening consent never requests microphone access').toBe(microphoneRequestsBeforeBrowserConsent)
  const availabilityChecksBeforeOpeningConsent = await page.evaluate(() => window.__voiceMock.availabilityChecks.length)
  await page.setViewportSize({ width: 390, height: 844 })
  const mobileDialogWidth = await browserConsent.evaluate(element => element.getBoundingClientRect().width)
  expect(mobileDialogWidth).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Voice consent dialog has no mobile horizontal overflow').toBeTruthy()
  await page.keyboard.press('Escape')
  await expect(browserConsent).toBeHidden()
  expect(await page.evaluate(() => window.__voiceMock.starts)).toBe(startsBeforeBrowserConsent)
  expect(await page.evaluate(() => window.__voiceMock.availabilityChecks.length), 'The browser service is not queried before explicit consent').toBe(availabilityChecksBeforeOpeningConsent)
  await page.setViewportSize({ width: 1440, height: 900 })
  await browserRecognitionOption.click()
  await expect(browserConsent, 'The inline recovery action can reopen the consent dialog after cancellation').toBeVisible()
  await page.keyboard.press('Escape')
  await expect(browserConsent).toBeHidden()

  await page.evaluate(() => { window.__voiceMock.browserAvailability = 'unavailable' })
  const microphoneRequestsBeforeUnavailableBrowserMode = await page.evaluate(() => window.__voiceMock.microphoneConstraints.length)
  await browserRecognitionOption.click()
  await browserConsent.getByRole('button', { name: 'Продолжить с браузером', exact: true }).click()
  await expect(status).toContainText('Браузер не подтвердил доступность русского распознавания')
  expect(await page.evaluate(() => window.__voiceMock.microphoneConstraints.length), 'An unavailable browser service is rejected before requesting microphone access').toBe(microphoneRequestsBeforeUnavailableBrowserMode)
  expect(await page.evaluate(() => window.__voiceMock.starts), 'A browser service reported unavailable must not start recognition').toBe(startsBeforeBrowserConsent)

  await page.evaluate(() => { window.__voiceMock.browserAvailability = 'available' })
  const availabilityChecksBeforeBrowserStart = await page.evaluate(() => window.__voiceMock.availabilityChecks.length)
  await browserRecognitionOption.click()
  await browserConsent.getByRole('button', { name: 'Продолжить с браузером', exact: true }).click()
  const browserRecognitionStop = composer.getByRole('button', { name: 'Завершить диктовку', exact: true })
  await expect(browserRecognitionStop).toBeVisible()
  const browserConfiguration = await page.evaluate(() => ({
    mode: window.__voiceMock.lastInstance.processLocally,
    language: window.__voiceMock.lastInstance.lang,
    availabilityChecks: window.__voiceMock.availabilityChecks.length,
  }))
  expect(browserConfiguration.mode, 'Browser-managed recognition starts only after the explicit consent action').toBe(false)
  expect(browserConfiguration.language).toBe('ru-RU')
  expect(browserConfiguration.availabilityChecks, 'Browser-managed availability is checked only after the consent action').toBe(availabilityChecksBeforeBrowserStart + 1)
  expect(await page.evaluate(() => window.__voiceMock.availabilityChecks.at(-1)), 'The opted-in check permits browser-managed processing but does not require a local pack').toEqual({ langs: ['ru-RU'], processLocally: false, quality: 'dictation' })
  await browserRecognitionStop.click()
  await expect(status).toContainText('Проверьте его перед отправкой')
  await expect(input).toHaveValue('Вопрос: сроки документ сроки')
  expect(messagePosts, 'Choosing browser recognition does not upload a recording or send the draft').toHaveLength(0)

  await page.setViewportSize({ width: 390, height: 844 })
  await showChat(page)
  await expect(composer.getByRole('button', { name: 'Голосовой ввод', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Voice status does not create mobile horizontal overflow').toBeTruthy()
  expect(messagePosts, 'Transcript is not submitted without an explicit send action').toHaveLength(0)
})
