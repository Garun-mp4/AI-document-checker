import assert from 'node:assert/strict'
import test from 'node:test'
import {
  insertTranscriptAtSelection,
  speechAvailabilityMessage,
  speechRecognitionErrorMessage,
} from '../src/speechInputCore.mjs'

test('speech transcript inserts at the saved caret without losing existing draft', () => {
  assert.deepEqual(
    insertTranscriptAtSelection('Вопрос: документ', 8, 8, 'сроки'),
    { value: 'Вопрос: сроки документ', caret: 14 },
  )
})

test('speech transcript replaces selected text and normalizes recognition whitespace', () => {
  assert.deepEqual(
    insertTranscriptAtSelection('План вопрос позже', 5, 11, '  сроки\n  ответа  '),
    { value: 'План сроки ответа позже', caret: 17 },
  )
})

test('empty transcript leaves the draft untouched and clamps invalid selection positions', () => {
  assert.deepEqual(insertTranscriptAtSelection('Черновик', -4, 300, ' \n '), {
    value: 'Черновик',
    caret: 8,
  })
  assert.deepEqual(insertTranscriptAtSelection('abc', 99, 99, 'd'), {
    value: 'abc d',
    caret: 5,
  })
})

test('local speech availability explains missing packs without silently downloading them', () => {
  assert.match(speechAvailabilityMessage('downloadable', 'ru-RU'), /Автозагрузка отключена/)
  assert.match(speechAvailabilityMessage('unavailable', 'ru-RU'), /недоступно/)
})

test('speech errors distinguish permission, missing microphone, language, and local engine failures', () => {
  assert.match(speechRecognitionErrorMessage('not-allowed'), /Нет доступа к микрофону/)
  assert.match(speechRecognitionErrorMessage('audio-capture'), /Микрофон не найден/)
  assert.match(speechRecognitionErrorMessage('language-not-supported'), /Аудио не отправлялось/)
  assert.match(speechRecognitionErrorMessage('network'), /Переключения на сетевой сервис нет/)
  assert.match(speechRecognitionErrorMessage('unknown'), /попробуйте ещё раз/)
})
