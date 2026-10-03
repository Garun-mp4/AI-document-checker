import capabilityCatalog from './document-formats.json'
import type { PreviewRenderer } from './types'

export type DocumentViewerKind = 'pdf' | 'docx' | 'text' | 'csv' | 'mapped'

type DocumentFormatCapability = {
  extension: string
  fileType: string
  label: string
  mimeTypes: string[]
  previewRenderer: PreviewRenderer
  viewer: DocumentViewerKind
}

// The JSON catalogue is also consumed by contract tests and the E2E format matrix.
export const SUPPORTED_DOCUMENT_FORMATS = capabilityCatalog.formats as unknown as readonly DocumentFormatCapability[]
export const ACCEPTED_DOCUMENT_EXTENSIONS = SUPPORTED_DOCUMENT_FORMATS.map(({ extension }) => extension)
export const SUPPORTED_DOCUMENT_EXTENSION_SET: ReadonlySet<string> = new Set(ACCEPTED_DOCUMENT_EXTENSIONS)
export const SUPPORTED_FORMAT_LABELS = [...new Set(SUPPORTED_DOCUMENT_FORMATS.map(({ label }) => label))]
export const SUPPORTED_FORMAT_LABELS_DOTTED = SUPPORTED_FORMAT_LABELS.join(' · ')
export const MAX_UPLOAD_BYTES = capabilityCatalog.maxUploadBytes

const capabilitiesByFileType = new Map(SUPPORTED_DOCUMENT_FORMATS.map((capability) => [capability.fileType, capability]))

function normalizeFileType(fileType: string): string {
  return fileType.trim().toLowerCase().replace(/^\./, '')
}

export function getDocumentPreviewRenderer(fileType: string): PreviewRenderer {
  return capabilitiesByFileType.get(normalizeFileType(fileType))?.previewRenderer ?? 'text'
}

export function getDocumentViewerKind(fileType: string): DocumentViewerKind {
  return capabilitiesByFileType.get(normalizeFileType(fileType))?.viewer ?? 'text'
}
