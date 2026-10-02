import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const app = await readFile(resolve(root, 'src/App.tsx'), 'utf8')
const styles = await readFile(resolve(root, 'src/styles.css'), 'utf8')

test('workspace layout offers three named radio choices and persists a validated mode', () => {
  assert.match(app, /type AnalysisLayoutMode = 'auto' \| 'stacked' \| 'split'/)
  assert.match(app, /ANALYSIS_LAYOUT_STORAGE_KEY\s*=\s*'document-checker-analysis-layout'/)
  assert.match(app, /stored === 'stacked' \|\| stored === 'split' \? stored : 'auto'/)
  assert.match(app, /localStorage\.setItem\(ANALYSIS_LAYOUT_STORAGE_KEY, analysisLayoutMode\)/)
  assert.match(app, /name="workspace-layout-mode" value="auto"/)
  assert.match(app, /name="workspace-layout-mode" value="stacked"/)
  assert.match(app, /name="workspace-layout-mode" value="split"/)
  for (const label of ['Авто', 'Документ сверху', 'Документ слева']) assert.ok(app.includes(`>${label}</span>`))
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

test('analysis cards adapt to their own column and controls show visible keyboard focus', () => {
  assert.match(styles, /\.insights-section\s*\{[^}]*container-type:\s*inline-size/)
  assert.match(styles, /@container\s*\(max-width:\s*720px\)[\s\S]*?\.insight-grid\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)/)
  assert.match(styles, /\.workspace-layout-option input:focus-visible \+ span\s*\{[^}]*outline:\s*2px solid var\(--ink\)/)
  assert.match(styles, /@media\s*\(max-width:\s*599px\)[\s\S]*?\.workspace-layout-fieldset/)
})
