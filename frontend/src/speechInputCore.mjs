const punctuationAtStart = /^[,.;:!?…)}\]]/u
const whitespaceAtEnd = /\s$/u
const whitespaceAtStart = /^\s/u

export function insertTranscriptAtSelection(value, selectionStart, selectionEnd, transcript) {
  const text = transcript.replace(/\s+/gu, ' ').trim()
  if (!text) return { value, caret: Math.max(0, Math.min(value.length, selectionEnd)) }

  const start = Math.max(0, Math.min(value.length, Math.min(selectionStart, selectionEnd)))
  const end = Math.max(start, Math.min(value.length, Math.max(selectionStart, selectionEnd)))
  const before = value.slice(0, start)
  const after = value.slice(end)
  const leadingSpace = before && !whitespaceAtEnd.test(before) && !punctuationAtStart.test(text) ? ' ' : ''
  const trailingSpace = after && !whitespaceAtStart.test(after) && !punctuationAtStart.test(after) ? ' ' : ''
  const inserted = `${leadingSpace}${text}${trailingSpace}`

  return {
    value: `${before}${inserted}${after}`,
    caret: before.length + inserted.length,
  }
}

export function speechAvailabilityMessage(availability, language) {
  if (availability === 'unavailable') {
    return 'Локальная русская диктовка недоступна в этом браузере. Можно продолжить печатать или выбрать распознавание браузером.'
  }
  return `Пакет ${language} для локальной диктовки не установлен или ещё загружается. Его можно установить вручную; автоматическая загрузка не запускается.`
}

export function speechRecognitionErrorMessage(error, mode = 'local') {
  switch (error) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Нет доступа к микрофону. Разрешите его в настройках браузера и повторите попытку.'
    case 'audio-capture':
      return 'Микрофон не найден или занят другим приложением.'
    case 'language-not-supported':
      return mode === 'local'
        ? 'Локальное распознавание этого языка недоступно. Аудио не отправлялось в интернет.'
        : 'Режим браузера не поддерживает русский язык. Можно попробовать ещё раз или продолжить печатать.'
    case 'no-speech':
      return 'Речь не распознана. Попробуйте ещё раз.'
    case 'network':
      return mode === 'local'
        ? 'Локальное распознавание завершилось с ошибкой. Переключения на сетевой сервис нет.'
        : 'Сервис распознавания браузера недоступен. Проверьте подключение к интернету и попробуйте ещё раз.'
    default:
      return 'Не удалось распознать речь. Проверьте микрофон и попробуйте ещё раз.'
  }
}
