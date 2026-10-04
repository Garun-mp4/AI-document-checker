import { useCallback, useEffect, useLayoutEffect, useRef, type FormEvent, type KeyboardEvent, type Ref } from 'react'
import { ArrowUp, ChevronDown, Mic, Plus, Square, X } from 'lucide-react'
import { useLocalSpeechInput } from '../useLocalSpeechInput'
import { VoiceLevelMeter } from './VoiceLevelMeter'

type AIComposerProps = {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onAttach: () => void
  onOpenModelSettings: () => void
  placeholder: string
  inputLabel: string
  helperText: string
  modelName: string
  reasoningName: string
  inputDisabled?: boolean
  sendDisabled?: boolean
  attachmentDisabled?: boolean
  voiceInputEnabled?: boolean
  inputRef?: Ref<HTMLTextAreaElement>
}

export function AIComposer({
  value,
  onChange,
  onSend,
  onAttach,
  onOpenModelSettings,
  placeholder,
  inputLabel,
  helperText,
  modelName,
  reasoningName,
  inputDisabled = false,
  sendDisabled = false,
  attachmentDisabled = false,
  voiceInputEnabled = true,
  inputRef,
}: AIComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const voiceControlRef = useRef<HTMLButtonElement>(null)
  const stopVoiceRef = useRef<HTMLButtonElement>(null)
  const assignTextareaRef = useCallback((element: HTMLTextAreaElement | null) => {
    textareaRef.current = element
    if (typeof inputRef === 'function') inputRef(element)
    else if (inputRef) inputRef.current = element
  }, [inputRef])
  const voiceInput = useLocalSpeechInput({
    value,
    onChange,
    textareaRef,
    disabled: inputDisabled,
    enabled: voiceInputEnabled,
  })

  const voiceActive = voiceInput.active
  const voiceLabel = voiceInput.state === 'recording'
    ? 'Остановить диктовку'
    : voiceInput.state === 'processing'
      ? 'Отменить распознавание'
      : voiceInput.state === 'requesting'
        ? 'Отменить запрос микрофона'
        : voiceInput.state === 'checking'
          ? 'Отменить проверку диктовки'
          : 'Голосовой ввод'
  const voiceTitle = voiceActive
    ? `${voiceLabel}. Нажмите Escape, чтобы отменить.`
    : 'Локальная диктовка на русском языке. Аудио не отправляется приложению.'
  const voiceStatusText = voiceInput.message || helperText
  const recording = voiceInput.state === 'recording'

  useEffect(() => {
    if (recording) stopVoiceRef.current?.focus({ preventScroll: true })
    else if (voiceInput.state === 'processing') voiceControlRef.current?.focus({ preventScroll: true })
    else if (['complete', 'cancelled', 'error'].includes(voiceInput.state)) {
      if (voiceInputEnabled) textareaRef.current?.focus({ preventScroll: true })
    }
  }, [recording, voiceInput.state, voiceInputEnabled])

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return

    textarea.style.height = 'auto'
    const maxHeight = 120
    textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden'
  }, [value])

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!inputDisabled && !sendDisabled) onSend()
  }

  const handleInputKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    if (!inputDisabled && !sendDisabled) onSend()
  }

  return (
    <form className={recording ? 'ai-composer is-recording' : 'ai-composer'} onSubmit={handleSubmit}>
      {recording ? (
        <div
          className="ai-composer-recording"
          data-voice-state="recording"
          data-level-available={voiceInput.meterAvailability === 'available'}
        >
          <div className="ai-composer-recording-bar">
            <button
              className="ai-composer-voice-cancel icon-button"
              type="button"
              aria-label="Отменить диктовку"
              title="Отменить диктовку — Escape"
              onClick={() => voiceInput.cancel()}
            >
              <X aria-hidden="true" size={19} strokeWidth={1.8} />
            </button>
            <VoiceLevelMeter analyser={voiceInput.meterAnalyser} recording={voiceInput.meterAvailability === 'available'} />
            <button
              ref={stopVoiceRef}
              className="ai-composer-voice-stop icon-button"
              type="button"
              aria-label="Завершить диктовку"
              title="Завершить и проверить распознанный текст"
              onClick={voiceInput.toggle}
            >
              <Square aria-hidden="true" size={16} strokeWidth={2} fill="currentColor" />
            </button>
          </div>
          <span
            className="ai-composer-recording-status"
            role="status"
            aria-live="polite"
            aria-atomic="true"
            aria-busy="true"
          >
            {voiceStatusText}
          </span>
        </div>
      ) : (
        <>
          <textarea
            ref={assignTextareaRef}
            rows={1}
            name="chat-message"
            autoComplete="off"
            value={value}
            onChange={(event) => {
              onChange(event.target.value)
              voiceInput.clearFeedback()
            }}
            onKeyDown={handleInputKeyDown}
            placeholder={placeholder}
            aria-label={inputLabel}
            maxLength={4_000}
            disabled={inputDisabled || voiceActive}
          />
          <div className="ai-composer-toolbar">
            <button
              className="ai-composer-attach icon-button"
              type="button"
              aria-label="Прикрепить документ"
              title="Добавить документ"
              onClick={onAttach}
              disabled={attachmentDisabled || voiceActive}
            >
              <Plus aria-hidden="true" size={21} strokeWidth={1.8} />
            </button>
            <span
              className="ai-composer-status"
              data-voice-state={voiceInput.state}
              role="status"
              aria-live="polite"
              aria-atomic="true"
              aria-busy={['checking', 'requesting', 'processing'].includes(voiceInput.state)}
              title={voiceStatusText}
            >
              {voiceStatusText}
            </span>
            <div className="ai-composer-controls">
              <button
                className="ai-composer-model-control"
                type="button"
                aria-haspopup="dialog"
                aria-label={`Модель ${modelName}, уровень анализа ${reasoningName}. Настроить`}
                title="Настроить модель и уровень анализа"
                onClick={onOpenModelSettings}
                disabled={voiceActive}
              >
                <span className="ai-composer-model-name">{modelName}</span>
                <span className="ai-composer-model-reasoning">{reasoningName}</span>
                <ChevronDown aria-hidden="true" size={15} />
              </button>
              <button
                ref={voiceControlRef}
                className={`ai-composer-voice icon-button ${voiceActive ? 'is-active' : ''}`}
                type="button"
                aria-label={voiceLabel}
                title={voiceTitle}
                aria-pressed={voiceActive}
                aria-keyshortcuts={voiceActive ? 'Escape' : undefined}
                aria-busy={['checking', 'requesting', 'processing'].includes(voiceInput.state)}
                onClick={voiceInput.toggle}
                disabled={inputDisabled || !voiceInputEnabled}
              >
                {voiceActive ? <Square aria-hidden="true" size={17} strokeWidth={2} fill="currentColor" /> : <Mic aria-hidden="true" size={21} strokeWidth={1.8} />}
              </button>
              <button
                className="ai-composer-send send-button"
                type="submit"
                aria-label="Отправить вопрос"
                title="Отправить вопрос"
                disabled={inputDisabled || sendDisabled || voiceActive}
              >
                <ArrowUp aria-hidden="true" size={20} strokeWidth={2} />
              </button>
            </div>
          </div>
        </>
      )}
    </form>
  )
}
