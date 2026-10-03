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
    return `Локальное распознавание ${language} недоступно в этом браузере. Можно продолжить печатать.`
  }
  return `Пакет ${language} для локальной диктовки не установлен или ещё загружается. Автозагрузка отключена; можно продолжить печатать.`
}

export function speechRecognitionErrorMessage(error) {
  switch (error) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Нет доступа к микрофону. Разрешите его в настройках браузера и повторите попытку.'
    case 'audio-capture':
      return 'Микрофон не найден или занят другим приложением.'
    case 'language-not-supported':
      return 'Локальное распознавание этого языка недоступно. Аудио не отправлялось в интернет.'
    case 'no-speech':
      return 'Речь не распознана. Попробуйте ещё раз.'
    case 'network':
      return 'Локальное распознавание завершилось с ошибкой. Переключения на сетевой сервис нет.'
    default:
      return 'Не удалось распознать речь. Проверьте микрофон и попробуйте ещё раз.'
  }
}
