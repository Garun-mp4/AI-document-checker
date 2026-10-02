import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const component = await readFile(resolve(root, 'src/components/LocalDataDialog.tsx'), 'utf8')
const app = await readFile(resolve(root, 'src/App.tsx'), 'utf8')
const styles = await readFile(resolve(root, 'src/styles.css'), 'utf8')

test('local data screen keeps cleanup actions separate and requires the server preview phrase', () => {
  assert.match(component, /startPlan\('clear_temp'\)/)
  assert.match(component, /startPlan\('clear_cache'\)/)
  assert.match(component, /startPlan\('delete_selected'\)/)
  assert.match(component, /startPlan\('delete_all'\)/)
  assert.match(component, /JSON\.stringify\(\{ plan_id: plan\.plan_id, confirmation: confirmation\.trim\(\) \}\)/)
  assert.match(component, /confirmation\.trim\(\) !== plan\.confirmation_phrase/)
  assert.match(component, /aria-modal="true"/)
  assert.match(component, /event\.key === 'Escape'/)
})

test('local data screen explains protected Codex authorization and exports diagnostics separately', () => {
  assert.match(component, /Авторизация Codex защищена/)
  assert.match(component, /diagnostics/)
  assert.match(component, /diagnostics\.zip/)
  assert.match(component, /text\/documents|текста документов/i)
  assert.match(app, /Управление локальными данными/)
  assert.match(component, /локальная модель .* сохранена/)
  assert.match(component, /embedding_cache_bytes/)
})

test('local data viewer uses one dialog scroll container and remains responsive', () => {
  assert.match(styles, /\.local-data-dialog\s*\{[^}]*width:\s*min\(100%,\s*900px\)/)
  assert.match(styles, /\.modal-card\s*\{[^}]*max-height:/)
  assert.match(styles, /\.local-data-document-list\s*\{[^}]*border-top/)
  assert.doesNotMatch(styles, /\.local-data-document-list\s*\{[^}]*overflow-y/)
  assert.doesNotMatch(styles, /\.local-data-plan-documents\s*\{[^}]*overflow-y/)
  assert.match(styles, /@media\s*\(max-width:\s*599px\)[\s\S]*?\.local-data-dialog/)
})
