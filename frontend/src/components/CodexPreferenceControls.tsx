import { useEffect, useRef, useState, type ChangeEvent, type CSSProperties, type KeyboardEvent } from 'react'
import { Popover } from '@base-ui/react/popover'
import { Select } from '@base-ui/react/select'
import { Check, ChevronDown, Zap } from 'lucide-react'
import type { CodexModelOption, CodexReasoningOption } from '../types'

const ALLOWED_MODELS = new Set(['gpt-6-luna', 'gpt-6.1-sol'])

export type CodexPreferenceControlsProps = {
  modelOptions: CodexModelOption[]
  selectedModelId: string
  reasoningOptions: CodexReasoningOption[]
  selectedReasoningEffort: string
  modelName: string
  reasoningName: string
  authenticated: boolean
  busy?: boolean
  disabled?: boolean
  onOpenSettings: () => void
  onSelectModel: (model: string) => Promise<boolean>
  onSelectReasoning: (effort: string) => Promise<boolean>
}

export function CodexPreferenceControls({
  modelOptions,
  selectedModelId,
  reasoningOptions,
  selectedReasoningEffort,
  modelName,
  reasoningName,
  authenticated,
  busy = false,
  disabled = false,
  onOpenSettings,
  onSelectModel,
  onSelectReasoning,
}: CodexPreferenceControlsProps) {
  const visibleModels = modelOptions.filter((option) => ALLOWED_MODELS.has(option.id.toLowerCase()))
  const selectedModel = visibleModels.find((option) => option.id === selectedModelId)
  const selectedEffortIndex = reasoningOptions.findIndex((option) => option.value === selectedReasoningEffort)
  const [effortOpen, setEffortOpen] = useState(false)
  const [draftEffortIndex, setDraftEffortIndex] = useState(selectedEffortIndex >= 0 ? selectedEffortIndex : 0)
  const pendingEffortRef = useRef<string | null>(null)
  const effortCommitTimerRef = useRef<number | null>(null)
  const pointerActiveRef = useRef(false)

  useEffect(() => {
    pendingEffortRef.current = null
    if (!effortOpen) setDraftEffortIndex(selectedEffortIndex >= 0 ? selectedEffortIndex : 0)
  }, [effortOpen, selectedEffortIndex, selectedReasoningEffort])

  useEffect(() => () => {
    if (effortCommitTimerRef.current !== null) window.clearTimeout(effortCommitTimerRef.current)
  }, [])

  const commitEffort = (index: number) => {
    const option = reasoningOptions[index]
    if (!option || option.value === selectedReasoningEffort || pendingEffortRef.current === option.value) return

    pendingEffortRef.current = option.value
    void onSelectReasoning(option.value).then((saved) => {
      if (saved) setEffortOpen(false)
      else pendingEffortRef.current = null
    })
  }

  const scheduleEffortCommit = (index: number) => {
    if (effortCommitTimerRef.current !== null) window.clearTimeout(effortCommitTimerRef.current)
    effortCommitTimerRef.current = window.setTimeout(() => {
      effortCommitTimerRef.current = null
      commitEffort(index)
    }, 220)
  }

  const handleEffortChange = (event: ChangeEvent<HTMLInputElement>) => {
    const nextIndex = Number(event.currentTarget.value)
    setDraftEffortIndex(nextIndex)
    if (!pointerActiveRef.current) scheduleEffortCommit(nextIndex)
  }

  const handleEffortKeyUp = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!['ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'End', 'Home', 'PageDown', 'PageUp'].includes(event.key)) return
    const nextIndex = Number(event.currentTarget.value)
    scheduleEffortCommit(nextIndex)
  }

  const modelPickerEnabled = authenticated && visibleModels.length > 0
  const effortPickerEnabled = authenticated && selectedModel !== undefined && reasoningOptions.length > 0
  const effortOption = reasoningOptions[draftEffortIndex] ?? reasoningOptions[0]
  const effortProgress = reasoningOptions.length > 1
    ? `${(draftEffortIndex / (reasoningOptions.length - 1)) * 100}%`
    : '0%'
  const rangeStyle = { '--range-progress': effortProgress } as CSSProperties

  return (
    <div className="ai-composer-preference-controls" role="group" aria-label="Параметры модели">
      {modelPickerEnabled ? (
        <Select.Root<string>
          value={selectedModel ? selectedModelId : null}
          onValueChange={(model) => { if (model !== null) void onSelectModel(model) }}
          items={visibleModels.map(({ id, label }) => ({ value: id, label }))}
          modal={false}
        >
          <Select.Trigger
            className="ai-composer-model-control"
            aria-label={`Модель: ${selectedModel?.label ?? modelName}`}
            title="Выбрать модель"
            disabled={busy || disabled}
            data-testid="composer-model-trigger"
          >
            <Select.Value className="ai-composer-model-name">
              {(value) => visibleModels.find((option) => option.id === value)?.label ?? modelName}
            </Select.Value>
            <ChevronDown aria-hidden="true" size={14} />
          </Select.Trigger>
          <Select.Portal>
            <Select.Positioner className="codex-model-positioner" side="top" align="end" sideOffset={8} alignItemWithTrigger={false}>
              <Select.Popup className="codex-model-popup">
                <Select.Label className="codex-picker-heading">Выбрать модель</Select.Label>
                <Select.List className="codex-model-options">
                  {visibleModels.map((option) => (
                    <Select.Item key={option.id} value={option.id} className="codex-model-option">
                      <span className="codex-model-option-copy">
                        <Select.ItemText className="codex-model-option-name">{option.label}</Select.ItemText>
                        {option.description && <small>{option.description}</small>}
                      </span>
                      {option.id === selectedModelId && <Check aria-hidden="true" size={16} />}
                    </Select.Item>
                  ))}
                </Select.List>
              </Select.Popup>
            </Select.Positioner>
          </Select.Portal>
        </Select.Root>
      ) : (
        <button
          className="ai-composer-model-control"
          type="button"
          aria-label={`Модель: ${modelName}. Открыть настройки Codex`}
          title={authenticated ? 'Разрешённая модель недоступна в этом аккаунте' : 'Подключить Codex и выбрать модель'}
          disabled={busy || disabled}
          onClick={onOpenSettings}
          data-testid="composer-model-trigger"
        >
          <span className="ai-composer-model-name">{modelName}</span>
          <ChevronDown aria-hidden="true" size={14} />
        </button>
      )}

      {effortPickerEnabled ? (
        <Popover.Root
          open={effortOpen}
          onOpenChange={(open) => {
            setEffortOpen(open)
            if (open) setDraftEffortIndex(selectedEffortIndex >= 0 ? selectedEffortIndex : 0)
          }}
          modal={false}
        >
          <Popover.Trigger
            className="ai-composer-effort-control"
            aria-label={`Уровень размышления: ${reasoningName}`}
            title="Настроить уровень размышления"
            disabled={busy || disabled}
            data-testid="composer-effort-trigger"
          >
            <span>{reasoningName}</span>
            <ChevronDown aria-hidden="true" size={14} />
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Positioner className="codex-effort-positioner" side="top" align="end" sideOffset={8}>
              <Popover.Popup className="codex-effort-popup" aria-labelledby="codex-effort-title">
                <div className="codex-effort-heading">
                  <Zap aria-hidden="true" size={17} />
                  <div>
                    <Popover.Title className="codex-effort-title" id="codex-effort-title">
                      {effortOption?.label ?? reasoningName}
                    </Popover.Title>
                    <span className="codex-effort-model">{selectedModel?.label ?? modelName}</span>
                  </div>
                </div>
                {reasoningOptions.length > 1 ? (
                  <div className="codex-effort-slider-wrap">
                    <input
                      className="codex-effort-slider"
                      type="range"
                      min={0}
                      max={reasoningOptions.length - 1}
                      step={1}
                      value={draftEffortIndex}
                      style={rangeStyle}
                      aria-label="Уровень размышления"
                      aria-valuetext={effortOption?.label ?? reasoningName}
                      aria-describedby="codex-effort-description"
                      disabled={busy || disabled}
                      data-testid="composer-effort-slider"
                      onChange={handleEffortChange}
                      onPointerDown={() => {
                        pointerActiveRef.current = true
                        if (effortCommitTimerRef.current !== null) window.clearTimeout(effortCommitTimerRef.current)
                      }}
                      onPointerUp={(event) => {
                        pointerActiveRef.current = false
                        const nextIndex = Number(event.currentTarget.value)
                        if (effortCommitTimerRef.current !== null) window.clearTimeout(effortCommitTimerRef.current)
                        effortCommitTimerRef.current = null
                        commitEffort(nextIndex)
                      }}
                      onPointerCancel={() => { pointerActiveRef.current = false }}
                      onKeyUp={handleEffortKeyUp}
                      onBlur={(event) => {
                        const nextIndex = Number(event.currentTarget.value)
                        if (effortCommitTimerRef.current !== null) window.clearTimeout(effortCommitTimerRef.current)
                        effortCommitTimerRef.current = null
                        commitEffort(nextIndex)
                      }}
                    />
                    <span className="codex-effort-ticks" aria-hidden="true">
                      {reasoningOptions.map((option) => <i key={option.value} />)}
                    </span>
                  </div>
                ) : <div className="codex-effort-single">Для этой модели доступен один уровень.</div>}
                <Popover.Description className="codex-effort-description" id="codex-effort-description">
                  {effortOption?.description || 'Уровень применяется к новым запросам.'}
                </Popover.Description>
                {busy && <span className="codex-effort-saving" role="status">Сохраняю настройку…</span>}
              </Popover.Popup>
            </Popover.Positioner>
          </Popover.Portal>
        </Popover.Root>
      ) : (
        <button
          className="ai-composer-effort-control"
          type="button"
          aria-label={`Уровень размышления: ${reasoningName}. Открыть настройки Codex`}
          title={authenticated ? 'Сначала выберите доступную модель' : 'Подключить Codex и выбрать уровень размышления'}
          disabled={busy || disabled}
          onClick={onOpenSettings}
          data-testid="composer-effort-trigger"
        >
          <span>{reasoningName}</span>
          <ChevronDown aria-hidden="true" size={14} />
        </button>
      )}
    </div>
  )
}
