import { gzipSync } from 'node:zlib'
import { readFile, stat } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = resolve(fileURLToPath(new URL('..', import.meta.url)))
const distRoot = resolve(frontendRoot, 'dist')
const manifest = JSON.parse(await readFile(resolve(distRoot, 'document-checker-manifest.json'), 'utf8'))
const baselineGzipBytes = 363_239
const maximumInitialGzipBytes = Math.floor(baselineGzipBytes * 0.8)
const entryKey = Object.keys(manifest).find((key) => manifest[key].isEntry && manifest[key].src === 'index.html')
const pdfKey = Object.keys(manifest).find((key) => key === 'src/components/PdfOriginalViewer.tsx')
const workerKey = Object.keys(manifest).find((key) => key.endsWith('/pdf.worker.min.mjs'))

if (!entryKey || !pdfKey || !workerKey) {
  throw new Error('Build manifest must contain the app entry, lazy PDF viewer, and separate PDF worker.')
}
if (!manifest[pdfKey].isDynamicEntry) {
  throw new Error('PdfOriginalViewer must remain a dynamic entry, separate from the initial app bundle.')
}

const initialEntries = new Set()
function collectStatic(entry) {
  if (initialEntries.has(entry)) return
  initialEntries.add(entry)
  for (const importedKey of manifest[entry]?.imports ?? []) collectStatic(importedKey)
}
collectStatic(entryKey)

const initialFiles = new Set([...initialEntries].map((key) => manifest[key].file).filter((file) => /\.(?:m?js)$/i.test(file)))
if (initialFiles.has(manifest[pdfKey].file)) {
  throw new Error('PDF viewer chunk was pulled into the initial JavaScript request graph.')
}
if (!(manifest[entryKey].dynamicImports ?? []).includes(pdfKey)) {
  throw new Error('App entry no longer exposes the PDF viewer as an on-demand import.')
}
if (initialFiles.has(manifest[workerKey].file)) {
  throw new Error('PDF worker must not be part of initial JavaScript requests.')
}

let initialRawBytes = 0
let initialGzipBytes = 0
for (const file of initialFiles) {
  const bytes = await readFile(resolve(distRoot, file))
  initialRawBytes += bytes.length
  initialGzipBytes += gzipSync(bytes, { level: 9 }).length
}

const pdfChunkPath = resolve(distRoot, manifest[pdfKey].file)
const workerPath = resolve(distRoot, manifest[workerKey].file)
const pdfChunkBytes = await readFile(pdfChunkPath)
const workerBytes = await stat(workerPath)
const reduction = (1 - initialGzipBytes / baselineGzipBytes) * 100
console.log(`Initial JS: ${initialRawBytes.toLocaleString('en-US')} bytes raw, ${initialGzipBytes.toLocaleString('en-US')} bytes gzip (baseline ${baselineGzipBytes.toLocaleString('en-US')}, ${reduction.toFixed(1)}% smaller; required ${maximumInitialGzipBytes.toLocaleString('en-US')} bytes or less).`)
console.log(`On-demand PDF viewer: ${pdfChunkBytes.length.toLocaleString('en-US')} bytes raw, ${gzipSync(pdfChunkBytes, { level: 9 }).length.toLocaleString('en-US')} bytes gzip; separate worker: ${workerBytes.size.toLocaleString('en-US')} bytes.`)

if (initialGzipBytes > maximumInitialGzipBytes) {
  throw new Error(`Initial JavaScript gzip budget exceeded: ${initialGzipBytes} > ${maximumInitialGzipBytes} bytes.`)
}
