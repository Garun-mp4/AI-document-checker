import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent, type Ref } from 'react'
import { ArrowUp, Mic, Plus, Square, X } from 'lucide-react'
import { useLocalSpeechInput } from '../useLocalSpeechInput'
import { CodexPreferenceControls } from './CodexPreferenceControls'
import type { CodexPreferenceControlsProps } from './CodexPreferenceControls'
import { VoiceLevelMeter } from './VoiceLevelMeter'

export type AIComposerPreferences = Omit<CodexPreferenceControlsProps, 'disabled' | 'onOpenSettings'> & {
  onOpenModelSettings: () => void
}

type AIComposerProps = AIComposerPreferences & {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onAttach: () => void
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
  modelOptions,
  selectedModelId,
  reasoningOptions,
  selectedReasoningEffort,
  authenticated,
  busy: preferencesBusy,
  onSelectModel,
  onSelectReasoning,
  inputDisabled = false,
  sendDisabled = false,
  attachmentDisabled = false,
  voiceInputEnabled = true,
  inputRef,
}: AIComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const voiceControlRef = useRef<HTMLButtonElement>(null)
  const stopVoiceRef = useRef<HTMLButtonElement>(null)
  const browserConsentDialogRef = useRef<HTMLDialogElement>(null)
  const [browserConsentOpen, setBrowserConsentOpen] = useState(false)
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
        : voiceInput.state === 'installing'
          ? 'Загружается локальный пакет'
          : 'Голосовой ввод'
  const voiceTitle = voiceActive
    ? `${voiceLabel}. Нажмите Escape, чтобы отменить.`
    : 'Сначала проверяется локальная диктовка. Если её нет, можно отдельно выбрать режим браузера, который может передавать аудио внешнему сервису.'
  const voiceStatusText = voiceInput.message || helperText
  const recording = voiceInput.state === 'recording'

  useEffect(() => {
    const dialog = browserConsentDialogRef.current
    if (!dialog) return
    if (browserConsentOpen && !dialog.open) dialog.showModal()
    else if (!browserConsentOpen && dialog.open) dialog.close()
  }, [browserConsentOpen])

  useEffect(() => {
    if (voiceInput.browserConsentRequired) setBrowserConsentOpen(true)
  }, [voiceInput.browserConsentRequired])

  useEffect(() => {
    if (browserConsentOpen || voiceInput.browserConsentRequired) return
    if (recording) stopVoiceRef.current?.focus({ preventScroll: true })
    else if (voiceInput.state === 'processing') voiceControlRef.current?.focus({ preventScroll: true })
    else if (['complete', 'cancelled', 'error'].includes(voiceInput.state)) {
      if (voiceInputEnabled) textareaRef.current?.focus({ preventScroll: true })
    }
  }, [browserConsentOpen, recording, voiceInput.browserConsentRequired, voiceInput.state, voiceInputEnabled])

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
            <div className="ai-composer-status-block">
              <span
                className="ai-composer-status"
                data-voice-state={voiceInput.state}
                role="status"
                aria-live="polite"
                aria-atomic="true"
                aria-busy={['checking', 'installing', 'requesting', 'processing'].includes(voiceInput.state)}
                title={voiceStatusText}
              >
                {voiceStatusText}
              </span>
              {(voiceInput.canInstallLocalPack || voiceInput.canUseBrowserRecognition) && <div className="ai-composer-voice-options">
                {voiceInput.canInstallLocalPack && <button
                  className="ai-composer-voice-option"
                  type="button"
                  onClick={() => void voiceInput.installLocalPack()}
                  disabled={voiceInput.state === 'installing'}
                >
                  {voiceInput.state === 'installing' ? 'Загружается пакет…' : 'Загрузить локальный пакет'}
                </button>}
                {voiceInput.canUseBrowserRecognition && <button
                  className="ai-composer-voice-option"
                  type="button"
                  onClick={() => setBrowserConsentOpen(true)}
                  disabled={voiceInput.state === 'installing'}
                >
                  Распознать браузером…
                </button>}
              </div>}
            </div>
            <div className="ai-composer-controls">
              <CodexPreferenceControls
                modelOptions={modelOptions}
                selectedModelId={selectedModelId}
                reasoningOptions={reasoningOptions}
                selectedReasoningEffort={selectedReasoningEffort}
                modelName={modelName}
                reasoningName={reasoningName}
                authenticated={authenticated}
                busy={preferencesBusy}
                disabled={voiceActive}
                onOpenSettings={onOpenModelSettings}
                onSelectModel={onSelectModel}
                onSelectReasoning={onSelectReasoning}
              />
              <button
                ref={voiceControlRef}
                className={`ai-composer-voice icon-button ${voiceActive ? 'is-active' : ''}`}
                type="button"
                aria-label={voiceLabel}
                title={voiceTitle}
                aria-pressed={voiceActive}
                aria-keyshortcuts={voiceActive ? 'Escape' : undefined}
                aria-busy={['checking', 'installing', 'requesting', 'processing'].includes(voiceInput.state)}
                onClick={voiceInput.toggle}
                disabled={inputDisabled || !voiceInputEnabled || voiceInput.state === 'installing'}
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
      <dialog
        ref={browserConsentDialogRef}
        className="ocr-impact-dialog speech-consent-dialog"
        aria-labelledby="speech-consent-title"
        aria-describedby="speech-consent-description"
        onCancel={(event) => {
          event.preventDefault()
          setBrowserConsentOpen(false)
        }}
        onClose={() => setBrowserConsentOpen(false)}
        onClick={(event) => {
          if (event.target === event.currentTarget) setBrowserConsentOpen(false)
        }}
      >
        <span className="ocr-impact-icon"><Mic aria-hidden="true" size={18} /></span>
        <h3 id="speech-consent-title">Распознавать речь браузером?</h3>
        <p id="speech-consent-description">Локальная русская диктовка в этом браузере недоступна. При выборе этого режима браузер может передавать аудио внешнему сервису; его поставщика и правила хранения приложение определить не может.</p>
        <p>Приложение не сохраняет и не загружает аудио на собственный сервер; локальная шкала уровня работает только в памяти вкладки. Распознанный текст сначала появится в поле сообщения и не отправится в чат, пока вы сами не нажмёте кнопку отправки. Согласие действует только для этой диктовки.</p>
        <div className="ocr-impact-actions">
          <button className="button button-light" type="button" autoFocus onClick={() => setBrowserConsentOpen(false)}>Отмена</button>
          <button className="button button-dark" type="button" onClick={() => {
            setBrowserConsentOpen(false)
            voiceInput.startBrowserRecognition()
          }}>Продолжить с браузером</button>
        </div>
      </dialog>
    </form>
  )
}
