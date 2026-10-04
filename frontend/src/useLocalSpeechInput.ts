import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  insertTranscriptAtSelection,
  speechAvailabilityMessage,
  speechRecognitionErrorMessage,
} from './speechInputCore.mjs'
import { openLocalMicrophoneAnalyzer } from './microphoneLevel.mjs'

const VOICE_LANGUAGE = 'ru-RU'

type VoiceState = 'idle' | 'checking' | 'installing' | 'requesting' | 'recording' | 'processing' | 'complete' | 'error' | 'cancelled'
type SpeechAvailability = 'available' | 'downloadable' | 'downloading' | 'unavailable'
type MeterAvailability = 'idle' | 'checking' | 'available' | 'unavailable'
type RecognitionMode = 'local' | 'browser-managed'

type SpeechAlternativeLike = { transcript?: string }
type SpeechResultLike = { isFinal: boolean; 0?: SpeechAlternativeLike }
type SpeechResultEventLike = Event & { resultIndex: number; results: ArrayLike<SpeechResultLike> }
type SpeechErrorEventLike = Event & { error?: string }

interface LocalSpeechRecognition extends EventTarget {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  processLocally?: boolean
  onstart: ((event: Event) => void) | null
  onresult: ((event: SpeechResultEventLike) => void) | null
  onerror: ((event: SpeechErrorEventLike) => void) | null
  onend: ((event: Event) => void) | null
  start(): void
  stop(): void
  abort(): void
}

type LocalSpeechRecognitionConstructor = {
  new(): LocalSpeechRecognition
  available?: (options: {
    langs: string[]
    processLocally: boolean
    quality: 'dictation'
  }) => Promise<SpeechAvailability>
  install?: (options: {
    langs: string[]
    processLocally: true
    quality: 'dictation'
  }) => Promise<boolean>
}

type SpeechRecognitionWindow = Window & {
  SpeechRecognition?: LocalSpeechRecognitionConstructor
  webkitSpeechRecognition?: LocalSpeechRecognitionConstructor
}

type UseLocalSpeechInputOptions = {
  value: string
  onChange: (value: string) => void
  textareaRef: { current: HTMLTextAreaElement | null }
  disabled: boolean
  enabled: boolean
}

export function useLocalSpeechInput({ value, onChange, textareaRef, disabled, enabled }: UseLocalSpeechInputOptions) {
  const [state, setState] = useState<VoiceState>('idle')
  const [message, setMessage] = useState('')
  const [meterAnalyser, setMeterAnalyser] = useState<AnalyserNode | null>(null)
  const [meterAvailability, setMeterAvailability] = useState<MeterAvailability>('idle')
  const [canInstallLocalPack, setCanInstallLocalPack] = useState(false)
  const [canUseBrowserRecognition, setCanUseBrowserRecognition] = useState(false)
  const stateRef = useRef<VoiceState>('idle')
  const recognitionRef = useRef<LocalSpeechRecognition | null>(null)
  const meterMonitorRef = useRef<Awaited<ReturnType<typeof openLocalMicrophoneAnalyzer>> | null>(null)
  const meterAvailabilityRef = useRef<MeterAvailability>('idle')
  const sessionActiveRef = useRef(false)
  const operationRef = useRef(0)
  const transcriptRef = useRef('')
  const errorMessageRef = useRef('')
  const localPackInstallActiveRef = useRef(false)
  const draftRef = useRef(value)
  const onChangeRef = useRef(onChange)
  const enabledRef = useRef(enabled)
  const disabledRef = useRef(disabled)
  const selectionRef = useRef({ start: value.length, end: value.length, value })

  useLayoutEffect(() => {
    draftRef.current = value
    onChangeRef.current = onChange
    enabledRef.current = enabled
    disabledRef.current = disabled
  }, [disabled, enabled, value, onChange])

  useLayoutEffect(() => {
    if (state !== 'cancelled') return
    const { start, end, value: selectedValue } = selectionRef.current
    const currentDraft = draftRef.current
    const sameDraft = currentDraft === selectedValue
    const selectionStart = Math.min(sameDraft ? start : currentDraft.length, currentDraft.length)
    const selectionEnd = Math.min(sameDraft ? end : currentDraft.length, currentDraft.length)
    const textarea = textareaRef.current
    if (!textarea?.isConnected) return
    if (enabledRef.current && !disabledRef.current) textarea.focus({ preventScroll: true })
    textarea.setSelectionRange(selectionStart, selectionEnd)
  }, [state, textareaRef])

  const publish = useCallback((nextState: VoiceState, nextMessage: string) => {
    stateRef.current = nextState
    setState(nextState)
    setMessage(nextMessage)
  }, [])

  const releaseMicrophoneMeter = useCallback(() => {
    const monitor = meterMonitorRef.current
    meterMonitorRef.current = null
    setMeterAnalyser(null)
    if (monitor) void monitor.dispose()
  }, [])

  const cancel = useCallback((reason = 'Диктовка отменена. Черновик не изменён.') => {
    if (!sessionActiveRef.current) return
    operationRef.current += 1
    sessionActiveRef.current = false
    transcriptRef.current = ''
    errorMessageRef.current = ''
    releaseMicrophoneMeter()
    const recognition = recognitionRef.current
    recognitionRef.current = null
    if (recognition) {
      recognition.onstart = null
      recognition.onresult = null
      recognition.onerror = null
      recognition.onend = null
      try {
        recognition.abort()
      } catch {
        // The browser may already have ended the recognition session.
      }
    }
    publish('cancelled', reason)
  }, [publish, releaseMicrophoneMeter, textareaRef])

  const finish = useCallback((recognition: LocalSpeechRecognition) => {
    if (!sessionActiveRef.current || recognitionRef.current !== recognition) return
    recognitionRef.current = null
    sessionActiveRef.current = false
    releaseMicrophoneMeter()
    const transcript = transcriptRef.current.trim()
    transcriptRef.current = ''
    const failure = errorMessageRef.current
    errorMessageRef.current = ''

    if (transcript) {
      const selection = selectionRef.current
      const currentDraft = draftRef.current
      const sameDraft = currentDraft === selection.value
      const start = sameDraft ? selection.start : currentDraft.length
      const end = sameDraft ? selection.end : currentDraft.length
      const insertion = insertTranscriptAtSelection(currentDraft, start, end, transcript)
      onChangeRef.current(insertion.value)
      window.requestAnimationFrame(() => {
        const textarea = textareaRef.current
        if (textarea?.isConnected) {
          textarea.focus({ preventScroll: true })
          textarea.setSelectionRange(insertion.caret, insertion.caret)
        }
      })
      publish('complete', failure
        ? 'Распознанный текст добавлен в черновик; запись была прервана.'
        : 'Текст добавлен в черновик. Проверьте его перед отправкой.')
      return
    }

    if (failure) publish('error', failure)
    else publish('error', 'Речь не распознана. Попробуйте ещё раз.')
  }, [publish, releaseMicrophoneMeter, textareaRef])

  const start = useCallback(async (mode: RecognitionMode = 'local') => {
    if (disabled || !enabled || sessionActiveRef.current || localPackInstallActiveRef.current) return
    const operation = operationRef.current + 1
    operationRef.current = operation
    sessionActiveRef.current = true
    transcriptRef.current = ''
    errorMessageRef.current = ''
    meterAvailabilityRef.current = 'checking'
    setMeterAvailability('checking')
    setMeterAnalyser(null)
    setCanInstallLocalPack(false)
    setCanUseBrowserRecognition(false)
    const textarea = textareaRef.current
    const currentValue = draftRef.current
    selectionRef.current = {
      start: textarea?.selectionStart ?? currentValue.length,
      end: textarea?.selectionEnd ?? currentValue.length,
      value: currentValue,
    }
    publish('checking', mode === 'local' ? 'Проверяю поддержку локальной диктовки…' : 'Запускаю распознавание речи браузером…')

    const browser = window as SpeechRecognitionWindow
    const Recognition = browser.SpeechRecognition ?? browser.webkitSpeechRecognition
    if (!Recognition) {
      sessionActiveRef.current = false
      publish('error', 'Локальная диктовка не поддерживается этим браузером. Можно продолжить печатать.')
      return
    }

    let recognition: LocalSpeechRecognition
    try {
      recognition = new Recognition()
    } catch {
      sessionActiveRef.current = false
      publish('error', 'Не удалось запустить локальную диктовку в этом браузере. Можно продолжить печатать.')
      return
    }
    if (mode === 'local') {
      if (!('processLocally' in recognition) || typeof Recognition.available !== 'function') {
        sessionActiveRef.current = false
        setCanUseBrowserRecognition(true)
        publish('error', 'Браузер не подтверждает локальную обработку речи. Можно продолжить печатать или выбрать режим распознавания браузером.')
        return
      }

      let availability: SpeechAvailability
      try {
        availability = await Recognition.available({
          langs: [VOICE_LANGUAGE],
          processLocally: true,
          quality: 'dictation',
        })
      } catch {
        if (operationRef.current !== operation || !sessionActiveRef.current) return
        sessionActiveRef.current = false
        setCanUseBrowserRecognition(true)
        publish('error', 'Не удалось проверить локальную диктовку. Проверьте настройки браузера или выберите режим распознавания браузером.')
        return
      }
      if (operationRef.current !== operation || !sessionActiveRef.current) return
      if (availability !== 'available') {
        sessionActiveRef.current = false
        meterAvailabilityRef.current = 'idle'
        setMeterAvailability('idle')
        setCanInstallLocalPack(
          (availability === 'downloadable' || availability === 'downloading')
          && typeof Recognition.install === 'function',
        )
        setCanUseBrowserRecognition(true)
        publish('error', speechAvailabilityMessage(availability, VOICE_LANGUAGE))
        return
      }

      recognition.processLocally = true
      if (recognition.processLocally !== true) {
        sessionActiveRef.current = false
        meterAvailabilityRef.current = 'idle'
        setMeterAvailability('idle')
        setCanUseBrowserRecognition(true)
        publish('error', 'Браузер не включил локальную обработку речи. Можно продолжить печатать или выбрать режим распознавания браузером.')
        return
      }
    } else {
      // This branch is called only from the explicit browser-recognition consent dialog.
      if ('processLocally' in recognition) {
        try {
          recognition.processLocally = false
        } catch {
          sessionActiveRef.current = false
          setCanUseBrowserRecognition(true)
          publish('error', 'Браузер не разрешил выбранный режим распознавания. Аудио не записывалось.')
          return
        }
        if (recognition.processLocally !== false) {
          sessionActiveRef.current = false
          setCanUseBrowserRecognition(true)
          publish('error', 'Браузер не включил выбранный режим распознавания. Микрофон не включался.')
          return
        }
      }
      if (typeof Recognition.available === 'function') {
        let browserAvailability: SpeechAvailability
        try {
          browserAvailability = await Recognition.available({
            langs: [VOICE_LANGUAGE],
            processLocally: false,
            quality: 'dictation',
          })
        } catch {
          if (operationRef.current !== operation || !sessionActiveRef.current) return
          sessionActiveRef.current = false
          setCanUseBrowserRecognition(true)
          publish('error', 'Браузер не смог проверить русский режим распознавания. Микрофон не включался.')
          return
        }
        if (operationRef.current !== operation || !sessionActiveRef.current) return
        if (browserAvailability !== 'available') {
          sessionActiveRef.current = false
          setCanUseBrowserRecognition(true)
          publish('error', 'Браузер не подтвердил доступность русского распознавания. Микрофон не включался; можно продолжить печатать.')
          return
        }
      }
    }

    recognition.lang = VOICE_LANGUAGE
    recognition.continuous = true
    recognition.interimResults = true
    recognition.maxAlternatives = 1

    publish('requesting', 'Запрашиваю доступ к микрофону…')
    let meterMonitor: Awaited<ReturnType<typeof openLocalMicrophoneAnalyzer>> | null = null
    try {
      meterMonitor = await openLocalMicrophoneAnalyzer()
    } catch {
      // The optional level meter must not prevent local speech recognition from starting.
    }
    if (operationRef.current !== operation || !sessionActiveRef.current) {
      if (meterMonitor) void meterMonitor.dispose()
      return
    }
    if (meterMonitor) {
      meterMonitorRef.current = meterMonitor
      meterAvailabilityRef.current = 'available'
      setMeterAvailability('available')
      setMeterAnalyser(meterMonitor.analyser)
    } else {
      meterAvailabilityRef.current = 'unavailable'
      setMeterAvailability('unavailable')
      setMessage(mode === 'local'
        ? 'Уровень микрофона недоступен. Продолжаю локальную диктовку…'
        : 'Уровень микрофона недоступен. Продолжаю распознавание браузером…')
    }

    recognitionRef.current = recognition
    recognition.onstart = () => {
      if (recognitionRef.current !== recognition) return
      const status = mode === 'browser-managed'
        ? 'Распознавание браузером: звук может обрабатываться внешним сервисом. Завершите диктовку, чтобы проверить текст.'
        : meterAvailabilityRef.current === 'available'
          ? 'Говорите: шкала показывает уровень микрофона. Нажмите «Завершить диктовку», чтобы проверить текст; Escape — отменить.'
          : 'Идёт диктовка; индикатор уровня недоступен. Нажмите «Завершить диктовку», чтобы проверить текст; Escape — отменить.'
      publish('recording', status)
    }
    recognition.onresult = (event) => {
      if (recognitionRef.current !== recognition) return
      const finalParts: string[] = []
      for (let index = 0; index < event.results.length; index += 1) {
        const result = event.results[index]
        const text = result?.[0]?.transcript
        if (result?.isFinal && text) finalParts.push(text)
      }
      transcriptRef.current = finalParts.join(' ')
    }
    recognition.onerror = (event) => {
      if (recognitionRef.current !== recognition) return
      errorMessageRef.current = speechRecognitionErrorMessage(event.error ?? '', mode)
      if (mode === 'browser-managed') setCanUseBrowserRecognition(true)
      releaseMicrophoneMeter()
    }
    recognition.onend = () => finish(recognition)
    try {
      recognition.start()
    } catch (error) {
      if (recognitionRef.current === recognition) recognitionRef.current = null
      sessionActiveRef.current = false
      releaseMicrophoneMeter()
      const name = error instanceof Error ? error.name : ''
      if (mode === 'browser-managed') setCanUseBrowserRecognition(true)
      publish('error', name === 'NotAllowedError'
        ? speechRecognitionErrorMessage('not-allowed')
        : mode === 'local'
          ? 'Не удалось включить микрофон. Проверьте разрешение браузера и попробуйте ещё раз.'
          : 'Не удалось запустить распознавание браузером. Проверьте микрофон, подключение к сети и попробуйте ещё раз.')
    }
  }, [disabled, enabled, finish, publish, releaseMicrophoneMeter, textareaRef])

  const installLocalPack = useCallback(async () => {
    if (disabled || !enabled || localPackInstallActiveRef.current) return
    const browser = window as SpeechRecognitionWindow
    const Recognition = browser.SpeechRecognition ?? browser.webkitSpeechRecognition
    if (!Recognition?.install || typeof Recognition.available !== 'function') {
      setCanInstallLocalPack(false)
      setCanUseBrowserRecognition(Boolean(Recognition))
      publish('error', 'Этот браузер не умеет устанавливать локальный пакет. Можно продолжить печатать или выбрать режим распознавания браузером.')
      return
    }

    localPackInstallActiveRef.current = true
    const operation = operationRef.current + 1
    operationRef.current = operation
    setCanInstallLocalPack(false)
    setCanUseBrowserRecognition(false)
    publish('installing', 'Загружаю локальный пакет русского языка. Микрофон пока не включён.')
    try {
      const installed = await Recognition.install({
        langs: [VOICE_LANGUAGE],
        processLocally: true,
        quality: 'dictation',
      })
      if (operationRef.current !== operation) return
      if (!installed) {
        setCanInstallLocalPack(true)
        setCanUseBrowserRecognition(true)
        publish('error', 'Браузер не установил локальный пакет. Можно повторить попытку позже или выбрать режим распознавания браузером.')
        return
      }

      const availability = await Recognition.available({
        langs: [VOICE_LANGUAGE],
        processLocally: true,
        quality: 'dictation',
      })
      if (operationRef.current !== operation) return
      if (availability === 'available') {
        setCanUseBrowserRecognition(false)
        publish('complete', 'Локальный пакет установлен. Нажмите микрофон ещё раз, чтобы начать диктовку.')
      } else {
        setCanInstallLocalPack(
          (availability === 'downloadable' || availability === 'downloading')
          && typeof Recognition.install === 'function',
        )
        setCanUseBrowserRecognition(true)
        publish('error', speechAvailabilityMessage(availability, VOICE_LANGUAGE))
      }
    } catch {
      if (operationRef.current !== operation) return
      setCanInstallLocalPack(true)
      setCanUseBrowserRecognition(true)
      publish('error', 'Не удалось загрузить локальный пакет. Проверьте соединение или выберите режим распознавания браузером.')
    } finally {
      localPackInstallActiveRef.current = false
    }
  }, [disabled, enabled, publish])

  const startBrowserRecognition = useCallback(() => {
    void start('browser-managed')
  }, [start])

  const toggle = useCallback(() => {
    if (!sessionActiveRef.current) {
      void start()
      return
    }
    if (stateRef.current === 'recording') {
      publish('processing', 'Распознаю речь…')
      releaseMicrophoneMeter()
      try {
        recognitionRef.current?.stop()
      } catch {
        cancel('Не удалось завершить запись. Черновик не изменён.')
      }
      return
    }
    cancel(stateRef.current === 'processing'
      ? 'Распознавание отменено. Черновик не изменён.'
      : 'Диктовка отменена. Черновик не изменён.')
  }, [cancel, publish, releaseMicrophoneMeter, start])

  useEffect(() => {
    if (!enabled) cancel('Диктовка отменена: чат закрыт. Черновик не изменён.')
    else if (disabled) cancel('Диктовка остановлена: поле ввода недоступно. Черновик не изменён.')
  }, [cancel, disabled, enabled])

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !sessionActiveRef.current) return
      event.preventDefault()
      event.stopPropagation()
      cancel()
    }
    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [cancel])

  useEffect(() => {
    const stopWhenHidden = () => {
      if (document.visibilityState === 'hidden') cancel('Диктовка остановлена при уходе со страницы. Черновик не изменён.')
    }
    window.addEventListener('pagehide', stopWhenHidden)
    document.addEventListener('visibilitychange', stopWhenHidden)
    return () => {
      window.removeEventListener('pagehide', stopWhenHidden)
      document.removeEventListener('visibilitychange', stopWhenHidden)
      operationRef.current += 1
      sessionActiveRef.current = false
      const meterMonitor = meterMonitorRef.current
      meterMonitorRef.current = null
      if (meterMonitor) void meterMonitor.dispose()
      const recognition = recognitionRef.current
      recognitionRef.current = null
      if (recognition) {
        recognition.onstart = null
        recognition.onresult = null
        recognition.onerror = null
        recognition.onend = null
        try {
          recognition.abort()
        } catch {
          // The browser may already have ended the recognition session.
        }
      }
    }
  }, [cancel])

  const clearFeedback = useCallback(() => {
    if (stateRef.current === 'complete' || stateRef.current === 'error' || stateRef.current === 'cancelled') {
      setCanInstallLocalPack(false)
      setCanUseBrowserRecognition(false)
      publish('idle', '')
    }
  }, [publish])
  const active = state === 'checking' || state === 'requesting' || state === 'recording' || state === 'processing'

  return {
    state,
    message,
    active,
    meterAnalyser,
    meterAvailability,
    canInstallLocalPack,
    canUseBrowserRecognition,
    installLocalPack,
    startBrowserRecognition,
    clearFeedback,
    toggle,
    cancel,
  }
}
