import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const app = await readFile(resolve(root, 'src/App.tsx'), 'utf8')
const types = await readFile(resolve(root, 'src/types.ts'), 'utf8')
const panel = await readFile(resolve(root, 'src/components/ProcessingStatusPanel.tsx'), 'utf8')
const versionHook = await readFile(resolve(root, 'src/useBuildVersion.ts'), 'utf8')
const vite = await readFile(resolve(root, 'vite.config.ts'), 'utf8')
const nginx = await readFile(resolve(root, 'nginx.conf'), 'utf8')
const styles = await readFile(resolve(root, 'src/styles.css'), 'utf8')

test('processing status is restored from the persisted server job, not a fake step counter', () => {
  assert.match(app, /api<ProcessingJob\[]>\(`\$\{API\}\/documents\/\$\{selectedId\}\/jobs`\)/)
  assert.match(app, /jobs\.find\(\(job\) => \['queued', 'running', 'cancelling'\]\.includes\(job\.state\)\)/)
  assert.match(app, /<ProcessingStatusPanel/)
  assert.match(app, /cancelProcessing/)
  assert.match(panel, /job\.queue_position/)
  assert.match(panel, /processed_pages/)
  assert.match(panel, /completed < 3/)
  assert.match(panel, /stage_elapsed_seconds < 15/)
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.processing-progress-track span \{ transition: none; \}/)
  assert.doesNotMatch(app, /processing-step/)
  assert.match(types, /queue_wait_seconds: number \| null/)
  assert.match(types, /stage_elapsed_seconds: number \| null/)
})

test('frontend compares the compiled bundle, no-store manifest and diagnostic API', () => {
  assert.match(versionHook, /fetch\('\/build-info\.json', \{ cache: 'no-store' \}\)/)
  assert.match(versionHook, /fetch\('\/api\/v1\/version', \{ cache: 'no-store' \}\)/)
  assert.match(versionHook, /buildsMatch\(FRONTEND_BUILD_INFO, manifest\)/)
  assert.match(versionHook, /buildsMatch\(manifest, api\)/)
  assert.match(vite, /fileName: 'build-info\.json'/)
  assert.match(vite, /__APP_BUILD_INFO__/)
  assert.match(nginx, /location = \/build-info\.json[\s\S]*?no-store/)
  assert.match(nginx, /location \/assets\/[\s\S]*?immutable/)
  assert.match(nginx, /location ~\* \\.mjs\$[\s\S]*?application\/javascript[\s\S]*?immutable/)
})
