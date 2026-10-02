import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { performance } from 'node:perf_hooks'
import { execFileSync, spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import os from 'node:os'
import { chromium } from '../frontend/node_modules/@playwright/test/index.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const fixtures = resolve(root, 'frontend/e2e/fixtures')
const baseUrl = (process.env.AI_CHECKER_BASE_URL || 'http://localhost:5174').replace(/\/$/, '')
const apiUrl = `${baseUrl}/api/v1`
const label = process.argv[2] || 'unlabeled'
if (!/^[a-z0-9-]{1,24}$/i.test(label)) throw new Error('Benchmark label must be a short alphanumeric value.')
const composeProject = process.env.M14_COMPOSE_PROJECT || 'document-checker-e2e'
const composeFile = process.env.M14_COMPOSE_FILE || 'compose.e2e.yml'
const documentIdsForCleanup = new Map()
const memorySamples = []
let memorySampler

function dockerOutput(args, timeout = 8_000) {
  try {
    return execFileSync('docker', args, { encoding: 'utf8', timeout, stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch {
    return null
  }
}

function parseMemoryBytes(value) {
  const match = value.match(/^([\d.]+)\s*(B|kB|KB|KiB|MB|MiB|GB|GiB|TB|TiB)/i)
  if (!match) return null
  const unit = match[2].toLowerCase()
  const multiplier = unit === 'b' ? 1 : unit === 'kb' || unit === 'mb' || unit === 'gb' || unit === 'tb'
    ? 1000 ** ({ kb: 1, mb: 2, gb: 3, tb: 4 }[unit])
    : 1024 ** ({ kib: 1, mib: 2, gib: 3, tib: 4 }[unit])
  return Math.round(Number(match[1]) * multiplier)
}

function startMemorySampler() {
  let child
  try {
    child = spawn('docker', ['stats', '--format', '{{json .}}'], { stdio: ['ignore', 'pipe', 'ignore'], windowsHide: true })
  } catch {
    return null
  }
  let resolveReady
  const ready = new Promise((resolve) => { resolveReady = resolve })
  const lines = createInterface({ input: child.stdout })
  lines.on('line', (line) => {
    try {
      const cleanLine = line.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '').trim()
      const container = JSON.parse(cleanLine)
      if (!container.Name?.startsWith(`${composeProject}-`)) return
      const [usedMemory, memoryLimit] = (container.MemUsage || '').split('/').map((value) => value.trim())
      const currentMemory = parseMemoryBytes(usedMemory || '')
      const limitMemory = parseMemoryBytes(memoryLimit || '')
      if (currentMemory !== null) memorySamples.push({
        sampled_at: new Date().toISOString(),
        container: container.Name,
        memory_bytes: currentMemory,
        memory_limit_bytes: limitMemory,
        cpu_percent: container.CPUPerc,
      })
      if (memorySamples.length) resolveReady()
    } catch {
      // Ignore transient Docker stats lines; the benchmark still reports the samples it collected.
    }
  })
  child.on('error', () => undefined)
  return {
    ready,
    stop: () => new Promise((resolveStop) => {
      if (child.exitCode !== null || child.killed) return resolveStop()
      child.once('exit', resolveStop)
      child.kill()
    }),
  }
}

function runtimeVersions() {
  const dockerServer = dockerOutput(['version', '--format', '{{.Server.Version}}'])
  const compose = dockerOutput(['compose', 'version', '--short'])
  const dependencyCode = "import importlib.metadata as m, json, sys; names=['fastapi','sqlalchemy','fastembed','markitdown','pypdf','pdfplumber','pytesseract']; print(json.dumps({'python':sys.version.split()[0], **{n:(m.version(n) if any(d.metadata['Name'].lower()==n for d in m.distributions()) else None) for n in names}}))"
  const backend = dockerOutput(['compose', '-p', composeProject, '-f', composeFile, 'exec', '-T', 'api', 'python', '-c', dependencyCode])
  let backendVersions = null
  try { backendVersions = JSON.parse(backend || 'null') } catch { backendVersions = null }
  const toolchain = {
    tesseract: dockerOutput(['compose', '-p', composeProject, '-f', composeFile, 'exec', '-T', 'worker', 'tesseract', '--version'])?.split(/\r?\n/)[0] || null,
    poppler: dockerOutput(['compose', '-p', composeProject, '-f', composeFile, 'exec', '-T', 'worker', 'sh', '-c', 'pdftoppm -v 2>&1'])?.split(/\r?\n/)[0] || null,
  }
  return { docker_server: dockerServer, docker_compose: compose, backend: backendVersions, toolchain }
}

async function jsonRequest(path, init) {
  const response = await fetch(`${apiUrl}${path}`, init)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(`${init?.method || 'GET'} ${path} failed (${response.status}): ${body.detail || response.statusText}`)
  return body
}

async function upload(name) {
  const bytes = await readFile(resolve(fixtures, name))
  const body = new FormData()
  body.append('file', new Blob([bytes]), name)
  const started = performance.now()
  const document = await jsonRequest('/documents', { method: 'POST', body })
  documentIdsForCleanup.set(name, document.id)
  return { document, upload_ms: Math.round(performance.now() - started), name, bytes: bytes.length }
}

async function waitForReady(entry) {
  const started = performance.now()
  let previousStage = null
  let stageStarted = started
  const stageMs = {}
  while (performance.now() - started < 240_000) {
    const [document, jobs] = await Promise.all([
      jsonRequest(`/documents/${entry.document.id}`),
      jsonRequest(`/documents/${entry.document.id}/jobs`),
    ])
    const job = jobs.find((item) => ['queued', 'running', 'cancelling'].includes(item.state)) || jobs[0]
    const stage = job?.stage || document.status
    const now = performance.now()
    if (previousStage !== null && stage !== previousStage) stageMs[previousStage] = (stageMs[previousStage] || 0) + now - stageStarted
    if (stage !== previousStage) {
      previousStage = stage
      stageStarted = now
    }
    if (document.status === 'ready') {
      if (previousStage) stageMs[previousStage] = (stageMs[previousStage] || 0) + performance.now() - stageStarted
      return {
        document,
        job,
        processing_ms: Math.round(performance.now() - started),
        observed_stage_ms: Object.fromEntries(Object.entries(stageMs).map(([key, value]) => [key, Math.round(value)])),
        processing_performance_ms: job?.progress?.performance_ms || {},
      }
    }
    if (['error', 'needs_auth', 'model_unavailable', 'cancelled'].includes(document.status)) {
      throw new Error(`${entry.name} ended in ${document.status}: ${document.error_message || job?.error || 'no detail'}`)
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 150))
  }
  throw new Error(`Timed out processing ${entry.name}`)
}

async function measureReadEndpoints(entry) {
  const times = {}
  const measure = async (name, path) => {
    const elapsed = []
    let result
    for (let run = 0; run < 4; run += 1) {
      const started = performance.now()
      result = await jsonRequest(path)
      elapsed.push(Math.round(performance.now() - started))
    }
    const sorted = [...elapsed].sort((left, right) => left - right)
    times[name] = {
      first_ms: elapsed[0],
      median_ms: Math.round((sorted[1] + sorted[2]) / 2),
      samples_ms: elapsed,
      bytes: Buffer.byteLength(JSON.stringify(result)),
    }
    return result
  }
  await measure('preview', `/documents/${entry.document.id}/preview`)
  const markdown = await measure('markdown_page', `/documents/${entry.document.id}/markdown?offset=0&limit=100000`)
  const scope = markdown.status === 'ready' ? 'markdown' : 'original'
  await measure('search', `/documents/${entry.document.id}/search?q=${encodeURIComponent('документ')}&scope=${scope}&offset=0&limit=20`)
  if (entry.name.endsWith('.csv')) await measure('table_page', `/documents/${entry.document.id}/preview/table?offset=0&limit=100`)
  return times
}

const records = []
let browser
try {
  const runtime = runtimeVersions()
  if (!runtime.docker_server || !runtime.backend) throw new Error('Не удалось прочитать версии Docker или backend-контейнера.')
  memorySampler = startMemorySampler()
  if (!memorySampler) throw new Error('Не удалось запустить Docker stats для измерения памяти.')
  await Promise.race([memorySampler.ready, new Promise((resolve) => setTimeout(resolve, 5_000))])
  if (!memorySamples.length) throw new Error(`Docker stats не вернул память контейнеров проекта ${composeProject}.`)
  await jsonRequest('/__e2e/provider', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' })
  const uploads = await Promise.all([upload('mixed.pdf'), upload('large.csv')])
  const processed = await Promise.all(uploads.map(waitForReady))
  for (let index = 0; index < uploads.length; index += 1) {
    records.push({
      file: uploads[index].name,
      input_bytes: uploads[index].bytes,
      upload_ms: uploads[index].upload_ms,
      processing_ms: processed[index].processing_ms,
      processing_performance_ms: processed[index].processing_performance_ms,
      observed_stage_ms: processed[index].observed_stage_ms,
      status: processed[index].document.status,
      chunk_count: processed[index].document.chunk_count,
      analysis_source: processed[index].document.analysis_source,
      ocr_status: processed[index].document.ocr_status,
      endpoints: await measureReadEndpoints(uploads[index]),
    })
  }

  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const pdf = uploads.find((item) => item.name.endsWith('.pdf'))
  const navigationStarted = performance.now()
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' })
  await page.evaluate((id) => {
    localStorage.setItem('document-checker-selected-chat', id)
    localStorage.removeItem('document-checker-new-chat')
  }, pdf.document.id)
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.locator('.pdf-page-sheet canvas').first().waitFor({ state: 'visible', timeout: 60_000 })
  const viewerMs = Math.round(performance.now() - navigationStarted)
  const browserSnapshot = await page.evaluate(() => ({
    dom_nodes: document.getElementsByTagName('*').length,
    canvas_count: document.querySelectorAll('.pdf-page-sheet canvas').length,
    pdf_canvas_bytes: Array.from(document.querySelectorAll('.pdf-page-sheet canvas')).reduce((sum, item) => sum + item.width * item.height * 4, 0),
    heap_used_bytes: performance.memory?.usedJSHeapSize ?? null,
    heap_limit_bytes: performance.memory?.jsHeapSizeLimit ?? null,
  }))
  await page.close()

  const version = await jsonRequest('/version')
  await memorySampler?.stop()
  memorySampler = null
  if (!memorySamples.length) throw new Error(`Docker stats не вернул память контейнеров проекта ${composeProject}.`)
  const peakMemory = Object.values(memorySamples.reduce((byContainer, sample) => {
    const previous = byContainer[sample.container]
    if (!previous || sample.memory_bytes > previous.memory_bytes) byContainer[sample.container] = sample
    return byContainer
  }, {}))
  const result = {
    label,
    base_url: baseUrl,
    measured_at: new Date().toISOString(),
    app_version: version,
    node_version: process.version,
    browser_version: browser.version(),
    runtime_versions: runtime,
    host: {
      platform: `${os.platform()} ${os.release()}`,
      architecture: os.arch(),
      cpu_model: os.cpus()[0]?.model || null,
      logical_cpu_count: os.cpus().length,
      memory_bytes: os.totalmem(),
    },
    browser_viewport: { width: 1440, height: 900 },
    viewer_first_render_ms: viewerMs,
    browser_snapshot: browserSnapshot,
    container_memory: {
      sample_interval_ms: 1_000,
      samples: memorySamples.length,
      peak_by_container: peakMemory,
    },
    scenario: {
      uploads: ['mixed.pdf', 'large.csv'],
      upload_concurrency: 2,
      endpoint_samples: 4,
      memory_sampling: 'continuous docker stats stream at approximately 1 second resolution',
    },
    documents: records,
  }
  const outputPath = resolve(root, 'docs/performance', `m14-${label}.json`)
  await mkdir(resolve(root, 'docs/performance'), { recursive: true })
  await writeFile(outputPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8')
  console.log(JSON.stringify({ ...result, output_path: outputPath }, null, 2))
} finally {
  await memorySampler?.stop()
  await browser?.close()
  for (const documentId of documentIdsForCleanup.values()) {
    await fetch(`${apiUrl}/documents/${documentId}`, { method: 'DELETE' }).catch(() => undefined)
  }
}
