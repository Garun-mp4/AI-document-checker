import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const app = await readFile(resolve(root, 'src/App.tsx'), 'utf8')
const styles = await readFile(resolve(root, 'src/styles.css'), 'utf8')
const selector = await readFile(resolve(root, 'src/components/DocumentLayoutSelector.tsx'), 'utf8')

test('workspace layout offers one accessible three-option selector and persists a validated mode', () => {
  assert.match(selector, /export type AnalysisLayoutMode = 'auto' \| 'stacked' \| 'split'/)
  assert.match(app, /ANALYSIS_LAYOUT_STORAGE_KEY\s*=\s*'document-checker-analysis-layout'/)
  assert.match(app, /stored === 'stacked' \|\| stored === 'split' \? stored : 'auto'/)
  assert.match(app, /localStorage\.setItem\(ANALYSIS_LAYOUT_STORAGE_KEY, analysisLayoutMode\)/)
  assert.match(app, /<DocumentLayoutSelector value=\{analysisLayoutMode\} onChange=\{setAnalysisLayoutMode\}/)
  assert.match(selector, /<Select\.Root<AnalysisLayoutMode>[\s\S]*?value=\{value\}[\s\S]*?onValueChange=/)
  assert.match(selector, /<Select\.Label[\s\S]*?Расположение/)
  assert.match(selector, /<Select\.Trigger[\s\S]*?data-testid="workspace-layout-trigger"/)
  assert.match(selector, /<Select\.Positioner[\s\S]*?alignItemWithTrigger=\{false\}/)
  assert.match(selector, /<Select\.List[\s\S]*?<Select\.Item[\s\S]*?<Select\.ItemText/)
  for (const label of ['Авто', 'Документ сверху', 'Документ слева']) assert.ok(selector.includes(`label: '${label}'`))
  for (const description of ['Автоматически подбирает оптимальное расположение', 'Документ сверху, чат снизу', 'Документ слева, чат справа']) assert.ok(selector.includes(description))
  assert.match(selector, /data-help-target="workspace\.layout\.select"/)
  assert.match(selector, /Sparkles/)
  assert.match(app, /data-layout-mode=\{analysisLayoutMode\}/)
})

test('layout uses the available workspace width and keeps tables full-width', () => {
  assert.match(styles, /\.workspace-scroll\s*\{[^}]*container-type:\s*inline-size/)
  assert.match(styles, /@container\s*\(max-width:\s*960px\)[\s\S]*?\.document-analysis-layout:not\(\[data-layout-mode="split"\]\)/)
  assert.match(styles, /@container\s*\(min-width:\s*760px\)[\s\S]*?\.document-analysis-layout\[data-layout-mode="split"\][\s\S]*?minmax\(320px,\s*\.43fr\)\s+minmax\(420px,\s*\.57fr\)/)
  assert.match(styles, /\.document-analysis-layout\[data-layout-mode="split"\][^\n]*\.insights-section\s*\{\s*margin-top:\s*0/)
  assert.match(styles, /@container\s*\(max-width:\s*759px\)[\s\S]*?workspace-layout-split-note\s*\{\s*display:\s*block/)
  assert.match(styles, /\.document-analysis-layout\.is-table-layout\s*\{\s*display:\s*block/)
  assert.match(styles, /\.workspace-layout-table-note\s*\{\s*display:\s*block/)
})

test('analysis cards adapt to their own column and selector adapts without clipping or motion under reduced-motion settings', () => {
  assert.match(styles, /\.insights-section\s*\{[^}]*container-type:\s*inline-size/)
  assert.match(styles, /@container\s*\(max-width:\s*720px\)[\s\S]*?\.insight-grid\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)/)
  assert.match(styles, /\.workspace-layout-trigger\s*\{[^}]*min-height:\s*40px/)
  assert.match(styles, /\.workspace-layout-option\[data-selected\]/)
  assert.match(styles, /\.workspace-layout-option:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--ink\)/)
  assert.match(styles, /\.workspace-layout-popup\s*\{[^}]*transform-origin:\s*var\(--transform-origin/)
  assert.match(styles, /\.workspace-layout-option-description\s*\{[^}]*overflow-wrap:\s*anywhere/)
  assert.match(styles, /@media\s*\(max-width:\s*599px\)[\s\S]*?\.workspace-layout-popup\s*\{[^}]*calc\(100vw - 20px\)/)
  assert.match(styles, /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?\.workspace-layout-popup\s*\{\s*transition:\s*opacity 100ms linear/)
})
