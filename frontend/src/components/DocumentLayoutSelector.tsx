import type { RefCallback } from 'react'
import { Select } from '@base-ui/react/select'
import { Check, ChevronDown, Sparkles } from 'lucide-react'

export type AnalysisLayoutMode = 'auto' | 'stacked' | 'split'

type DocumentLayoutSelectorProps = {
  value: AnalysisLayoutMode
  onChange: (value: AnalysisLayoutMode) => void
  helpTargetRef: RefCallback<HTMLElement>
}

const LAYOUT_OPTIONS: { value: AnalysisLayoutMode; label: string; description: string }[] = [
  { value: 'auto', label: 'Авто', description: 'Автоматически подбирает оптимальное расположение' },
  { value: 'stacked', label: 'Документ сверху', description: 'Документ сверху, чат снизу' },
  { value: 'split', label: 'Документ слева', description: 'Документ слева, чат справа' },
]

const SELECT_ITEMS = LAYOUT_OPTIONS.map(({ label, value }) => ({ label, value }))

function LayoutModeIcon({ mode }: { mode: AnalysisLayoutMode }) {
  if (mode === 'auto') return <Sparkles aria-hidden="true" size={19} strokeWidth={1.8} />

  return (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
      <path d={mode === 'stacked' ? 'M4 12h16' : 'M12 4v16'} />
    </svg>
  )
}

export function DocumentLayoutSelector({ value, onChange, helpTargetRef }: DocumentLayoutSelectorProps) {
  return (
    <div ref={helpTargetRef} data-help-target="workspace.layout.select" className="workspace-layout-control">
      <Select.Root<AnalysisLayoutMode>
        value={value}
        onValueChange={(nextValue) => { if (nextValue !== null) onChange(nextValue) }}
        items={SELECT_ITEMS}
        modal={false}
      >
        <Select.Label className="workspace-layout-label">
          Расположение
        </Select.Label>
        <Select.Trigger className="workspace-layout-trigger" data-testid="workspace-layout-trigger">
          <LayoutModeIcon mode={value} />
          <Select.Value className="workspace-layout-value" />
          <Select.Icon className="workspace-layout-chevron">
            <ChevronDown aria-hidden="true" size={16} strokeWidth={1.9} />
          </Select.Icon>
        </Select.Trigger>
        <Select.Portal>
          <Select.Positioner className="workspace-layout-positioner" side="bottom" align="start" sideOffset={6} alignItemWithTrigger={false}>
            <Select.Popup className="workspace-layout-popup">
              <Select.List className="workspace-layout-options">
                {LAYOUT_OPTIONS.map((option) => (
                  <Select.Item key={option.value} value={option.value} className="workspace-layout-option">
                    <LayoutModeIcon mode={option.value} />
                    <span className="workspace-layout-option-copy">
                      <Select.ItemText className="workspace-layout-option-title">{option.label}</Select.ItemText>
                      <span className="workspace-layout-option-description">{option.description}</span>
                    </span>
                    {value === option.value && <Check className="workspace-layout-option-check" aria-hidden="true" size={18} strokeWidth={2} />}
                  </Select.Item>
                ))}
              </Select.List>
            </Select.Popup>
          </Select.Positioner>
        </Select.Portal>
      </Select.Root>
    </div>
  )
}
