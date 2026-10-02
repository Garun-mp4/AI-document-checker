import { useCallback, useLayoutEffect, useRef, type FormEvent, type KeyboardEvent, type Ref } from 'react'
import { ArrowUp, ChevronDown, Mic, Plus } from 'lucide-react'

type AIComposerProps = {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onAttach: () => void
  onOpenModelSettings: () => void
  onVoiceInput: () => void
  placeholder: string
  inputLabel: string
  helperText: string
  modelName: string
  reasoningName: string
  inputDisabled?: boolean
  sendDisabled?: boolean
  attachmentDisabled?: boolean
  inputRef?: Ref<HTMLTextAreaElement>
}

export function AIComposer({
  value,
  onChange,
  onSend,
  onAttach,
  onOpenModelSettings,
  onVoiceInput,
  placeholder,
  inputLabel,
  helperText,
  modelName,
  reasoningName,
  inputDisabled = false,
  sendDisabled = false,
  attachmentDisabled = false,
  inputRef,
}: AIComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const assignTextareaRef = useCallback((element: HTMLTextAreaElement | null) => {
    textareaRef.current = element
    if (typeof inputRef === 'function') inputRef(element)
    else if (inputRef) inputRef.current = element
  }, [inputRef])

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
    <form className="ai-composer" onSubmit={handleSubmit}>
      <textarea
        ref={assignTextareaRef}
        rows={1}
        name="chat-message"
        autoComplete="off"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleInputKeyDown}
        placeholder={placeholder}
        aria-label={inputLabel}
        maxLength={4_000}
        disabled={inputDisabled}
      />
      <div className="ai-composer-toolbar">
        <button
          className="ai-composer-attach icon-button"
          type="button"
          aria-label="Прикрепить документ"
          title="Добавить документ"
          onClick={onAttach}
          disabled={attachmentDisabled}
        >
          <Plus aria-hidden="true" size={21} strokeWidth={1.8} />
        </button>
        <span className="ai-composer-status">{helperText}</span>
        <div className="ai-composer-controls">
          <button
            className="ai-composer-model-control"
            type="button"
            aria-haspopup="dialog"
            aria-label={`Модель ${modelName}, уровень анализа ${reasoningName}. Настроить`}
            title="Настроить модель и уровень анализа"
            onClick={onOpenModelSettings}
          >
            <span className="ai-composer-model-name">{modelName}</span>
            <span className="ai-composer-model-reasoning">{reasoningName}</span>
            <ChevronDown aria-hidden="true" size={15} />
          </button>
          <button
            className="ai-composer-voice icon-button"
            type="button"
            aria-label="Голосовой ввод"
            title="Функция голосового ввода находится в разработке"
            onClick={onVoiceInput}
          >
            <Mic aria-hidden="true" size={21} strokeWidth={1.8} />
          </button>
          <button
            className="ai-composer-send send-button"
            type="submit"
            aria-label="Отправить вопрос"
            title="Отправить вопрос"
            disabled={inputDisabled || sendDisabled}
          >
            <ArrowUp aria-hidden="true" size={20} strokeWidth={2} />
          </button>
        </div>
      </div>
    </form>
  )
}
