export type DocumentStatus =
  | 'queued'
  | 'extracting'
  | 'indexing'
  | 'analyzing'
  | 'ready'
  | 'needs_auth'
  | 'model_unavailable'
  | 'error'

export interface DocumentRecord {
  id: string
  filename: string
  file_type: string
  file_size: number
  status: DocumentStatus
  error_message: string | null
  chunk_count: number
  metadata: Record<string, unknown>
  markdown_status: 'pending' | 'ready' | 'fallback' | 'failed' | 'legacy'
  analysis_source: 'markitdown' | 'native_fallback'
  markdown_error: string | null
  markdown_converter_version: string | null
  markdown_char_count: number
  markdown_line_count: number
  markdown_checksum: string | null
  markdown_mapping: Record<string, number>
  created_at: string
  updated_at: string
}

export interface ChatSummary {
  id: string
  document_id: string
  title: string
  filename: string
  file_type: string
  file_size: number
  status: DocumentStatus
  error_message: string | null
  chunk_count: number
  metadata: Record<string, unknown>
  created_at: string
  last_activity_at: string
  message_count: number
  last_message_at: string | null
  last_message_preview: string | null
}

export interface SourceRef {
  id: string
  text: string
  locator: Record<string, unknown>
  ordinal: number
  is_derived: boolean
}

export type PreviewLayout = 'pdf' | 'paper' | 'table' | 'tree' | 'slides'
export type PreviewBlockKind = 'page' | 'paragraph' | 'table' | 'row' | 'node' | 'text' | 'calculation'
export type PreviewRenderer = 'pdf' | 'docx' | 'text' | 'csv' | 'xml' | 'xlsx' | 'xls' | 'pptx' | 'html' | 'json' | 'epub'

export interface PreviewBlock {
  id: string
  source_id: string
  ordinal: number
  kind: PreviewBlockKind
  text: string
  locator: SourceRef['locator']
  rows: string[][] | null
}

export interface DocumentPreview {
  document_id: string
  file_type: string
  renderer: PreviewRenderer
  layout: PreviewLayout
  aspect_ratio: number
  page_count: number | null
  original_url: string | null
  encoding: string | null
  source_count: number
  blocks: PreviewBlock[]
  total_blocks: number
  truncated: boolean
}

export interface MarkdownDocument {
  document_id: string
  status: 'pending' | 'ready' | 'fallback' | 'failed' | 'legacy'
  source: 'markitdown' | 'native_fallback'
  converter_version: string | null
  markdown: string
  offset: number
  limit: number
  total_chars: number
  total_lines: number
  checksum: string | null
  mapping_quality: Record<string, number>
  error: string | null
}

export interface TablePreviewRow {
  number: number
  cells: string[]
}

export interface TablePreview {
  columns: string[]
  rows: TablePreviewRow[]
  offset: number
  limit: number
  total_rows: number
}

export interface Insight {
  id: string
  key: string
  question: string
  answer: string
  citations: SourceRef[]
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: SourceRef[]
  created_at: string
}

export interface ChatRecord {
  id: string
  document_id: string
}

export interface CodexStatus {
  authenticated: boolean
  model: string
  model_label: string
  reasoning_effort: string
  model_available: boolean
  reasoning_available: boolean
  models: CodexModelOption[]
  login_state: 'idle' | 'pending' | 'completed' | 'failed'
  login_error: string | null
  verification_url: string | null
  user_code: string | null
  error: string | null
}

export interface CodexModelOption {
  id: string
  label: string
  description: string
  reasoning_efforts: CodexReasoningOption[]
}

export interface CodexReasoningOption {
  value: string
  label: string
  description: string
}

export interface StreamCitation {
  label: string
  id: string
  text: string
  locator: SourceRef['locator']
  ordinal: number
  is_derived: boolean
}
