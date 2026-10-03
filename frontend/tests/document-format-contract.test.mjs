import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const capabilityCatalog = JSON.parse(await readFile(resolve(root, 'src/document-formats.json'), 'utf8'))
const formats = capabilityCatalog.formats
const app = await readFile(resolve(root, 'src/App.tsx'), 'utf8')
const formatHelpers = await readFile(resolve(root, 'src/documentFormats.ts'), 'utf8')
const originalViewer = await readFile(resolve(root, 'src/components/OriginalDocumentViewer.tsx'), 'utf8')
const e2e = await readFile(resolve(root, 'e2e/documents.spec.mjs'), 'utf8')

const expectedExtensions = [
  '.pdf', '.docx', '.txt', '.md', '.csv', '.xml', '.xlsx', '.xls', '.pptx',
  '.html', '.htm', '.json', '.epub',
]

test('frontend catalog exposes exactly the 13 supported extensions and one HTML family label', () => {
  assert.deepEqual(formats.map(({ extension }) => extension), expectedExtensions)
  assert.equal(capabilityCatalog.maxUploadBytes, 25 * 1024 * 1024)
  assert.equal(new Set(formats.map(({ label }) => label)).size, 12)
  assert.equal(formats.filter(({ label }) => label === 'HTML').length, 2)
  assert.ok(formats.every(({ mimeTypes, previewRenderer, viewer }) => mimeTypes.length && previewRenderer && viewer))
  for (const excluded of ['.xlsm', '.xlsb', '.png', '.jpg', '.zip']) {
    assert.equal(formats.some(({ extension }) => extension === excluded), false)
  }
})

test('upload accept, local validation, labels, and size limit derive from the catalog helpers', () => {
  assert.ok(app.includes("ACCEPTED_DOCUMENT_EXTENSIONS.join(',')"))
  assert.match(app, /accept=\{ACCEPTED\}/)
  assert.match(app, /SUPPORTED_DOCUMENT_EXTENSION_SET\.has\(extension\)/)
  assert.match(app, /SUPPORTED_FORMAT_LABELS\.join\(', '\)/)
  assert.match(app, /SUPPORTED_FORMAT_LABELS_DOTTED/)
  assert.match(app, /MAX_UPLOAD_BYTES/)
  assert.match(formatHelpers, /SUPPORTED_DOCUMENT_FORMATS\.map\(\(\{ extension \}\) => extension\)/)
  assert.match(formatHelpers, /new Set\(SUPPORTED_DOCUMENT_FORMATS\.map\(\(\{ label \}\) => label\)\)/)
  assert.match(formatHelpers, /MAX_UPLOAD_BYTES = capabilityCatalog\.maxUploadBytes/)
})

test('original viewer routing and E2E cases use the same renderer and viewer capabilities', () => {
  assert.match(originalViewer, /getDocumentPreviewRenderer\(record\.file_type\)/)
  assert.match(originalViewer, /getDocumentViewerKind\(record\.file_type\)/)
  assert.match(originalViewer, /viewerKind === 'pdf'/)
  assert.match(originalViewer, /viewerKind === 'docx'/)
  assert.match(originalViewer, /viewerKind === 'csv'/)
  assert.match(originalViewer, /viewerKind === 'mapped'/)
  assert.match(e2e, /document-formats\.json/)
  assert.match(e2e, /for \(const \{ fileType, viewer, previewRenderer \} of formatCapabilities\)/)
  assert.match(e2e, /expect\(preview\.renderer\)\.toBe\(previewRenderer\)/)
})
