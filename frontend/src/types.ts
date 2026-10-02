export type DocumentStatus =
  | 'queued'
  | 'extracting'
  | 'ocr'
  | 'indexing'
  | 'analyzing'
  | 'ready'
  | 'needs_auth'
  | 'model_unavailable'
  | 'cancelled'
  | 'error'

export interface ProcessingJob {
  id: string
  operation: 'process' | 'analysis'
  version: number
  state: 'queued' | 'running' | 'cancelling' | 'cancelled' | 'succeeded' | 'failed'
  stage: string
  progress: Record<string, number>
  attempts: number
  max_attempts: number
  queued_at: string
  started_at: string | null
  stage_started_at: string | null
  queue_position: number | null
  queue_wait_seconds: number | null
  stage_elapsed_seconds: number | null
  error: string | null
  created_at: string
  finished_at: string | null
}

export interface DocumentRecord {
  active_version: number
  id: string
  filename: string
  file_type: string
  file_size: number
  status: DocumentStatus
  error_message: string | null
  chunk_count: number
  metadata: Record<string, unknown>
  markdown_status: 'pending' | 'ready' | 'fallback' | 'failed' | 'legacy'
  analysis_source: 'markitdown' | 'native_fallback' | 'ocr'
  markdown_error: string | null
  markdown_converter_version: string | null
  markdown_char_count: number
  markdown_line_count: number
  markdown_checksum: string | null
  markdown_mapping: Record<string, number>
  ocr_status: 'not_needed' | 'processing' | 'ready' | 'partial' | 'failed'
  ocr_language: string | null
  ocr_page_count: number | null
  ocr_confidence: number | null
  ocr_error: string | null
  ocr_engine_version: string | null
  ocr_char_count: number
  created_at: string
  updated_at: string
}

export interface ChatSummary {
  id: string
  document_id: string
  title: string
  custom_title: string | null
  pinned: boolean
  revision: number
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
  search_snippet: string | null
  search_message_id: string | null
}

export interface ChatLibraryPage {
  items: ChatSummary[]
  total: number
  offset: number
  limit: number
  has_more: boolean
}

export interface ChatSettings {
  id: string
  document_id: string
  title: string
  custom_title: string | null
  pinned: boolean
  revision: number
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
  source: 'markitdown' | 'native_fallback' | 'ocr'
  converter_version: string | null
  markdown: string
  offset: number
  limit: number
  line_offset: number
  total_chars: number
  total_lines: number
  checksum: string | null
  mapping_quality: Record<string, number>
  error: string | null
}

export type DocumentSearchScope = 'original' | 'markdown'

export interface DocumentSearchMatch {
  id: string
  text: string
  snippet: string
  locator: SourceRef['locator']
  ordinal: number
  is_derived: boolean
  match_start: number | null
  match_end: number | null
  markdown_start: number | null
  markdown_end: number | null
}

export interface DocumentSearchResponse {
  document_id: string
  scope: DocumentSearchScope
  query: string
  total: number
  offset: number
  limit: number
  matches: DocumentSearchMatch[]
}

export interface TablePreviewRow {
  number: number
  cells: string[]
  formula_cells: TableFormulaCell[]
}

export interface TableFormulaCell {
  column_index: number
  formula: string
  has_cached_value: boolean
}

export type TableFilterKind = 'text' | 'number' | 'empty'
export type TableFilterOperator = 'contains' | 'equals' | 'gt' | 'gte' | 'lt' | 'lte' | 'is_empty' | 'is_not_empty'

export interface TableFilter {
  column_index: number
  kind: TableFilterKind
  operator: TableFilterOperator
  value: string
}

export interface TablePreview {
  columns: string[]
  rows: TablePreviewRow[]
  offset: number
  limit: number
  total_rows: number
  filtered_rows: number
  sheet: string | null
  available_sheets: string[]
  delimiter: string | null
  column_kinds: Array<'empty' | 'date' | 'number' | 'text'>
  formula_policy: 'not_applicable' | 'detected' | 'cached_values_only'
  sort_column: number | null
  sort_direction: 'asc' | 'desc' | null
  filter: TableFilter | null
  focus_row: number | null
  focus_row_visible: boolean | null
}

export interface TableAggregate {
  count: number
  non_empty_count: number
  numeric_count: number
  nonnumeric_count: number
  formula_count: number
  formula_cache_missing_count: number
  sum: string | null
  average: string | null
  minimum: string | null
  maximum: string | null
  scope: 'document' | 'current_filter'
  source_row_count: number
  source_row_start: number | null
  source_row_end: number | null
}

export interface TableCalculation {
  sheet: string | null
  available_sheets: string[]
  column_index: number
  column: string
  filter: TableFilter | null
  formula_policy: 'not_applicable' | 'detected' | 'cached_values_only'
  rounding_rule: string
  document: TableAggregate
  filtered: TableAggregate
  document_source: SourceRef
  filtered_source: SourceRef
}

export interface Insight {
  id: string
  key: string
  question: string
  answer: string
  citations: SourceRef[]
  version?: number | null
  source_version?: number | null
  model?: string | null
  reasoning_effort?: string | null
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: SourceRef[]
  created_at: string
  context_epoch?: number
  reply_to_message_id?: string | null
  generation_status?: 'complete' | 'streaming' | 'interrupted'
  generation_error?: string | null
  model?: string | null
  reasoning_effort?: string | null
  source_version?: number | null
}

export interface ChatRecord {
  id: string
  document_id: string
  context_epoch?: number
}

export interface DocumentAnalysisVersion {
  number: number
  source_version: number
  state: string
  model: string | null
  reasoning_effort: string | null
  created_at: string
  is_active: boolean
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
