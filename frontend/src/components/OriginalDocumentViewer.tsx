import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown, BookOpen, Calculator, ChevronDown, Filter, LoaderCircle, Settings2, TriangleAlert, X } from 'lucide-react'
import { renderAsync } from 'docx-preview'
import * as pdfjsLib from 'pdfjs-dist'
import { useAppHelpTargetRef } from '../appHelpUiTargets'
import { getDocumentPreviewRenderer, getDocumentViewerKind } from '../documentFormats'
import type { DocumentPreview, DocumentRecord, PreviewBlock, SourceRef, StreamCitation, TableAggregate, TableCalculation, TableFilter, TableFilterKind, TablePreview } from '../types'

const API = '/api/v1'
const CSV_PAGE_SIZE = 100
const TABLE_ROW_HEIGHT = 36
const TABLE_HEADER_HEIGHT = 40
const TABLE_OVERSCAN_ROWS = 10
const TABLE_VIRTUALIZE_AFTER = 80

export interface OcrReprocessOptions {
  language: 'rus' | 'eng' | 'rus+eng'
  quality: 'fast' | 'balanced' | 'high'
  pages: number[] | null
}

type OcrLanguage = OcrReprocessOptions['language']
type OcrQuality = OcrReprocessOptions['quality']

const OCR_QUALITY: Record<OcrQuality, { label: string; dpi: number; description: string }> = {
  fast: { label: 'Быстро', dpi: 150, description: '150 DPI · быстрее, подходит для крупных букв' },
  balanced: { label: 'Сбалансированно', dpi: 200, description: '200 DPI · рекомендуемый баланс скорости и читаемости' },
  high: { label: 'Высокое качество', dpi: 300, description: '300 DPI · медленнее, помогает мелкому тексту' },
}

interface OcrPageInfo {
  page: number
  classification: string
  nativeClassification: string
  ocrResult: string
  error: string | null
  confidence: number | null
  language: string | null
  dpi: number | null
}

// Vite copies the worker as a separate asset. Keeping it out of the main
// bundle avoids a blank PDF viewer when the browser blocks an inline worker.
// The version query also invalidates a cached response if nginx's MIME map
// was changed after an earlier build.
const pdfWorkerUrl = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url)
pdfWorkerUrl.searchParams.set('v', 'pdfjs-4')
pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl.toString()

type ViewerSource = SourceRef | StreamCitation | PreviewBlock

interface OriginalDocumentViewerProps {
  document: DocumentRecord
  preview: DocumentPreview
  selectedSource: ViewerSource | null
  selectedSourceId: string | null
  originalUrl: string
  pageNumber: number
  onReprocess?: (options: OcrReprocessOptions) => Promise<boolean> | boolean
  reprocessing?: boolean
}

function sourceQuery(source: ViewerSource | null): string {
  if (!source) return ''
  return source.text.replace(/\s+/g, ' ').trim()
}

function renderCellMatch(value: string, start: number | null, end: number | null) {
  if (start === null || end === null || start < 0 || end <= start) return value || '—'
  const characters = Array.from(value)
  const from = Math.min(start, characters.length)
  const to = Math.min(end, characters.length)
  if (to <= from) return value || '—'
  return <>{characters.slice(0, from).join('')}<mark className="document-search-highlight" data-search-match="true">{characters.slice(from, to).join('')}</mark>{characters.slice(to).join('')}</>
}

type MatchQuality = 'exact' | 'approximate' | 'page_only' | 'not_found' | 'calculation'

interface NormalizedRange {
  start: number
  end: number
}

function normalizedRange(text: string, query: string, startHint = 0): NormalizedRange | null {
  const normalized: string[] = []
  const starts: number[] = []
  const ends: number[] = []
  let inWhitespace = false
  let offset = 0
  for (const character of text) {
    const transformed = character.normalize('NFKC').toLocaleLowerCase()
    if (/\s/u.test(character) || /\s/u.test(transformed)) {
      if (normalized.length && !inWhitespace) {
        normalized.push(' '); starts.push(offset); ends.push(offset + character.length)
      } else if (normalized.length && inWhitespace) ends[ends.length - 1] = offset + character.length
      inWhitespace = true
      offset += character.length
      continue
    }
    inWhitespace = false
    for (let index = 0; index < transformed.length; index += 1) {
      const item = transformed[index]
      normalized.push(item); starts.push(offset); ends.push(offset + character.length)
    }
    offset += character.length
  }
  const needle = query.normalize('NFKC').toLocaleLowerCase().trim().replace(/\s+/gu, ' ')
  if (!needle) return null
  const haystack = normalized.join('')
  let best: number | null = null
  let bestDistance = Number.POSITIVE_INFINITY
  let from = 0
  while (from <= haystack.length - needle.length) {
    const found = haystack.indexOf(needle, from)
    if (found < 0) break
    const distance = Math.abs(starts[found] - startHint)
    if (distance < bestDistance) { best = found; bestDistance = distance }
    from = found + 1
  }
  return best === null ? null : { start: starts[best], end: ends[best + needle.length - 1] }
}

function lineOffsetsFor(text: string): number[] {
  const offsets = [0]
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === '\r') {
      if (text[index + 1] === '\n') index += 1
      offsets.push(index + 1)
    } else if (text[index] === '\n') offsets.push(index + 1)
  }
  return offsets
}

function codePointOffsetToUtf16(text: string, codePointOffset: number): number {
  if (codePointOffset <= 0) return 0
  let codePoints = 0
  let utf16Offset = 0
  for (const character of text) {
    if (codePoints >= codePointOffset) break
    codePoints += 1
    utf16Offset += character.length
  }
  return utf16Offset
}

interface HighlightRegistryLike { set(name: string, value: unknown): void; delete(name: string): boolean }
interface HighlightConstructorLike { new (...ranges: Range[]): unknown }

function installTextHighlight(name: string, ranges: Range[]): boolean {
  const registry = (globalThis.CSS as unknown as { highlights?: HighlightRegistryLike } | undefined)?.highlights
  const HighlightConstructor = (globalThis as typeof globalThis & { Highlight?: HighlightConstructorLike }).Highlight
  if (!registry || !HighlightConstructor || !ranges.length) return false
  registry.set(name, new HighlightConstructor(...ranges))
  return true
}

function rangesWithinElement(element: HTMLElement, query: string): Range[] {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  let text = ''
  while (walker.nextNode()) {
    const node = walker.currentNode as Text
    nodes.push(node)
    text += node.data
  }
  const match = normalizedRange(text, query)
  if (!match) return []
  const ranges: Range[] = []
  let cursor = 0
  let range: Range | null = null
  for (const node of nodes) {
    const nodeStart = cursor
    const nodeEnd = cursor + node.data.length
    cursor = nodeEnd
    const from = Math.max(match.start, nodeStart)
    const to = Math.min(match.end, nodeEnd)
    if (to <= from) continue
    if (!range) range = document.createRange()
    range.setStart(node, from - nodeStart)
    range.setEnd(node, to - nodeStart)
    ranges.push(range)
    range = null
  }
  return ranges
}

function isCalculation(source: ViewerSource | null): boolean {
  return Boolean(source && (
    ('is_derived' in source && source.is_derived) ||
    source.locator.source_type === 'calculation' || source.locator.derived === true
  ))
}

interface PdfOcrWordBox {
  key: number
  left: number
  top: number
  width: number
  height: number
}

function pdfOcrWordBoxes(source: ViewerSource | null): PdfOcrWordBox[] {
  const rawMap = source?.locator.ocr_map
  if (!rawMap || typeof rawMap !== 'object') return []
  const map = rawMap as Record<string, unknown>
  const scale = map.coordinate_scale
  const rawBoxes = map.word_boxes
  if (typeof scale !== 'number' || scale <= 0 || !Array.isArray(rawBoxes)) return []
  return rawBoxes.flatMap((raw, key) => {
    if (!Array.isArray(raw) || raw.length < 8 || !raw.slice(0, 8).every((value) => typeof value === 'number' && Number.isFinite(value))) return []
    const [x0, y0, x1, y1] = raw as number[]
    if (x1 <= x0 || y1 <= y0) return []
    return [{
      key,
      left: x0 / scale * 100,
      top: y0 / scale * 100,
      width: (x1 - x0) / scale * 100,
      height: (y1 - y0) / scale * 100,
    }]
  })
}

function normalizedIncludes(value: string, query: string): boolean {
  if (!query) return false
  return value.replace(/\s+/g, ' ').toLocaleLowerCase().includes(query.replace(/\s+/g, ' ').toLocaleLowerCase())
}

function textSourceQueries(preview: DocumentPreview, source: ViewerSource | null): string[] {
  // XML/JSON Markdown blocks can link several separate values. Their combined
  // source_text is not contiguous in the original (tags/keys remain visible).
  if (['xml', 'json'].includes(preview.renderer) && source) {
    return source.text.split(/\n+/).map((part) => {
      const value = part.trim()
      return preview.renderer === 'xml' ? value.replace(/\s+\([^()]*=[^()]*\)$/, '') : value
    }).filter(Boolean)
  }
  return [sourceQuery(source)].filter(Boolean)
}

function scrollIntoViewRespectingMotion(target: Element | null | undefined, block: ScrollLogicalPosition = 'center') {
  if (!target) return
  const reduceMotion = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block })
}

function navigateToSource(target: Element | null | undefined) {
  const viewer = document.getElementById('document-original-viewer')
  scrollIntoViewRespectingMotion(viewer, 'nearest')
  window.requestAnimationFrame(() => scrollIntoViewRespectingMotion(target, 'center'))
}

function locatorLabel(source: ViewerSource): string {
  const label = source.locator.label
  if (typeof label === 'string' && label) return label
  if (typeof source.locator.page === 'number') return `Страница ${source.locator.page}`
  if (typeof source.locator.row_start === 'number') {
    const end = typeof source.locator.row_end === 'number' ? source.locator.row_end : source.locator.row_start
    return `Строки ${source.locator.row_start}–${end}`
  }
  if (typeof source.locator.line_start === 'number') {
    const end = typeof source.locator.line_end === 'number' ? source.locator.line_end : source.locator.line_start
    return `Строки ${source.locator.line_start}–${end}`
  }
  if (typeof source.locator.path === 'string') return source.locator.path
  return 'Фрагмент документа'
}

function SourceCallout({ source, quality, activeVersion, onReprocess, reprocessing = false }: { source: ViewerSource | null; quality: MatchQuality; activeVersion: number; onReprocess?: () => void; reprocessing?: boolean }) {
  if (!source) return null
  const matchQuality = isCalculation(source) ? 'calculation' : quality
  const needsOcrRecovery = source.locator.ocr === true && pdfOcrWordBoxes(source).length === 0
  const column = typeof source.locator.column === 'string' ? source.locator.column : null
  const rowStart = typeof source.locator.row_start === 'number' ? source.locator.row_start : null
  const rowEnd = typeof source.locator.row_end === 'number' ? source.locator.row_end : rowStart
  const sourceVersion = typeof source.locator.processing_version === 'number' ? source.locator.processing_version : null
  const isOlderVersion = sourceVersion !== null && activeVersion > 0 && sourceVersion < activeVersion
  const explanation: Record<MatchQuality, string> = {
    exact: 'Найден и подсвечен исходный текстовый диапазон.',
    approximate: source.locator.source_type === 'slide_block' || source.locator.source_type === 'chapter_block'
      ? 'Показан связанный извлечённый блок. Структурированный просмотр не сохраняет исходное оформление слайда или книги.'
      : 'Показан соответствующий абзац или исходный блок; точный диапазон недоступен.',
    page_only: 'Открыта связанная страница, строка или структурированный блок без точной подсветки текста.',
    not_found: 'Точное место в оригинале не найдено. Ниже показан сохранённый фрагмент источника.',
    calculation: 'Это локальный расчёт приложения. В оригинале показан исходный диапазон данных, из которого он рассчитан.',
  }
  return (
    <div className="preview-source-callout" role="status">
      <div className="preview-source-callout-heading"><BookOpen size={14} /><span>{matchQuality === 'calculation' ? 'Расчёт приложения' : 'Выбран источник'} · {locatorLabel(source)}</span></div>
      <p>{source.text}</p>
      {matchQuality === 'calculation' && <small className="source-calculation-input">{column ? `Столбец «${column}»` : 'Область расчёта'}{rowStart !== null ? ` · исходные строки ${rowStart}–${rowEnd}` : ''}</small>}
      {isOlderVersion && <small>Источник сохранён в версии обработки {sourceVersion}; сейчас активна версия {activeVersion}. Исходная цитата и её координаты сохранены.</small>}
      {needsOcrRecovery
        ? <><small>У этого старого результата OCR сохранена страница, но нет координат точной подсветки. Повторная обработка создаст карту координат.</small>
          {onReprocess && <button className="button button-light preview-reprocess-button" type="button" onClick={onReprocess} disabled={reprocessing}>{reprocessing ? <><LoaderCircle size={14} className="spin" /> Создаю карту координат…</> : 'Создать карту координат'}</button>}</>
        : <small>{explanation[matchQuality]}</small>}
    </div>
  )
}

function RenderError({ preview, originalUrl, message, onRetry }: { preview: DocumentPreview; originalUrl: string; message?: string | null; onRetry: () => void }) {
  return (
    <div className="preview-render-error" role="alert">
      <TriangleAlert size={18} />
      <div><strong>Не удалось отобразить оригинал</strong><span>Документ готов, но браузер не смог построить просмотр.</span>{message && <small>{message}</small>}</div>
      <div className="preview-render-actions"><button type="button" className="button button-light" onClick={onRetry}>Повторить</button><a href={originalUrl} target="_blank" rel="noreferrer">Открыть файл</a></div>
      {preview.blocks[0] && <div className="preview-fallback"><span>Текстовый контекст источников</span><p>{preview.blocks[0].text}</p></div>}
    </div>
  )
}

function TextOriginalViewer({ preview, originalUrl, selectedSource, onMatch, onError }: { preview: DocumentPreview; originalUrl: string; selectedSource: ViewerSource | null; onMatch: (value: MatchQuality) => void; onError: (message: string) => void }) {
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const linesRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let active = true
    setText(null)
    setError(null)
    void fetch(originalUrl).then(async (response) => {
      if (!response.ok) throw new Error('Файл недоступен')
      const data = await response.arrayBuffer()
      const encoding = preview.encoding || 'utf-8'
      let decoded: string
      try { decoded = new TextDecoder(encoding).decode(data) } catch { decoded = new TextDecoder().decode(data) }
      if (active) setText(decoded)
    }).catch((reason: unknown) => { if (active) { const message = reason instanceof Error ? reason.message : 'Не удалось загрузить текст'; setError(message); onError(message) } })
    return () => { active = false }
  }, [originalUrl, preview.encoding])

  useEffect(() => {
    if (!selectedSource || !linesRef.current) return
    if (isCalculation(selectedSource)) { onMatch('calculation'); return }
    if (!text) { onMatch('not_found'); return }
    const queries = textSourceQueries(preview, selectedSource)
    const lineStart = typeof selectedSource.locator.line_start === 'number' ? selectedSource.locator.line_start : null
    const lineEnd = typeof selectedSource.locator.line_end === 'number' ? selectedSource.locator.line_end : lineStart
    const rawCharStart = typeof selectedSource.locator.char_start === 'number' ? selectedSource.locator.char_start : null
    const rawCharEnd = typeof selectedSource.locator.char_end === 'number' ? selectedSource.locator.char_end : null
    const charStart = rawCharStart === null ? null : codePointOffsetToUtf16(text, rawCharStart)
    const charEnd = rawCharEnd === null ? null : codePointOffsetToUtf16(text, rawCharEnd)
    const nodes = Array.from(linesRef.current.querySelectorAll<HTMLElement>('[data-line]'))
    const starts = lineOffsetsFor(text)
    let target: HTMLElement | undefined
    const exactSearchRange = selectedSource.locator.search_match === true
      ? charStart !== null && charEnd !== null && charStart >= 0 && charEnd > charStart && charEnd <= text.length
        ? { start: charStart, end: charEnd }
        : queries.map((query) => normalizedRange(text, query, charStart ?? 0)).find(Boolean) || null
      : null
    const range = exactSearchRange || (charStart !== null && charEnd !== null && charStart >= 0 && charEnd > charStart && charEnd <= text.length
      ? { start: charStart, end: charEnd }
      : queries.map((query) => normalizedRange(text, query, charStart ?? 0)).find(Boolean) || null)
    for (const node of nodes) {
      const number = Number(node.dataset.line)
      if (!target && lineStart !== null && number >= lineStart && number <= (lineEnd ?? lineStart)) target = node
      if (!target && range) {
        const lineText = node.querySelector('code')?.textContent || ''
        const index = number - 1
        if (index >= 0 && range.start < starts[index] + lineText.length && range.end > starts[index]) target = node
      }
    }
    const pathAvailable = typeof selectedSource.locator.path === 'string' && Boolean(selectedSource.locator.path)
    const lineAvailable = lineStart !== null
    const exact = Boolean(range && (charStart !== null || queries.some((query) => normalizedRange(text, query, range.start))))
    onMatch(exact ? 'exact' : target ? 'approximate' : pathAvailable || lineAvailable ? 'page_only' : 'not_found')
    navigateToSource(target)
  }, [text, selectedSource, onMatch, preview.renderer])

  if (error) return <div className="preview-inline-error">{error}</div>
  if (text === null) return <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Загружаю оригинальный текст…</div>
  const queries = textSourceQueries(preview, selectedSource)
  const lineStart = typeof selectedSource?.locator.line_start === 'number' ? selectedSource.locator.line_start : null
  const lineEnd = typeof selectedSource?.locator.line_end === 'number' ? selectedSource.locator.line_end : lineStart
  const lines = text.split(/\r\n|\r|\n/)
  const lineOffsets = lineOffsetsFor(text)
  const rawCharStart = typeof selectedSource?.locator.char_start === 'number' ? selectedSource.locator.char_start : null
  const rawCharEnd = typeof selectedSource?.locator.char_end === 'number' ? selectedSource.locator.char_end : null
  const charStart = rawCharStart === null ? null : codePointOffsetToUtf16(text, rawCharStart)
  const charEnd = rawCharEnd === null ? null : codePointOffsetToUtf16(text, rawCharEnd)
  const exactSearchRange = selectedSource?.locator.search_match === true
    ? charStart !== null && charEnd !== null && charStart >= 0 && charEnd > charStart && charEnd <= text.length
      ? { start: charStart, end: charEnd }
      : queries.map((query) => normalizedRange(text, query, charStart ?? 0)).find(Boolean) || null
    : null
  const exactRange = exactSearchRange || (charStart !== null && charEnd !== null && charStart >= 0 && charEnd > charStart && charEnd <= text.length
    ? { start: charStart, end: charEnd }
    : queries.map((query) => normalizedRange(text, query, charStart ?? 0)).find(Boolean) || null)
  return (
    <div className="original-text-viewer" ref={linesRef}>
      {lines.map((line, index) => {
        const number = index + 1
        const inRange = lineStart !== null && number >= lineStart && number <= (lineEnd ?? lineStart)
        const lineOffset = lineOffsets[index] ?? 0
        const start = exactRange ? Math.max(0, exactRange.start - lineOffset) : -1
        const end = exactRange ? Math.min(line.length, exactRange.end - lineOffset) : -1
        const hasExactText = start >= 0 && end > start
        const match = queries.some((query) => normalizedIncludes(line, query))
        const content = hasExactText
          ? <>{line.slice(0, start)}<mark className="document-search-highlight" data-search-match="true">{line.slice(start, end)}</mark>{line.slice(end) || (!line.slice(start, end) ? ' ' : '')}</>
          : match || (inRange && selectedSource) ? <mark>{line || ' '}</mark> : line || ' '
        return <div className={`source-text-line ${inRange ? 'source-line-range' : ''}`} data-line={number} key={number}>
          <span className="source-line-number">{number}</span>
          <code>{content}</code>
        </div>
      })}
    </div>
  )
}

interface PdfTextItem { str: string; left: number; top: number; width: number; height: number }

function isPdfCancellation(reason: unknown): boolean {
  return reason instanceof Error && (reason.name === 'RenderingCancelledException' || reason.name === 'AbortException')
}

function PdfOriginalViewer({ originalUrl, pageNumber, selectedSource, onMatch }: { originalUrl: string; pageNumber: number; selectedSource: ViewerSource | null; onMatch: (value: MatchQuality) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const sheetRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<HTMLDivElement>(null)
  const textSpansRef = useRef(new Map<number, HTMLSpanElement>())
  const [pdf, setPdf] = useState<pdfjsLib.PDFDocumentProxy | null>(null)
  const [items, setItems] = useState<PdfTextItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [nativeFallback, setNativeFallback] = useState(false)
  const [rendering, setRendering] = useState(true)
  const [viewerWidth, setViewerWidth] = useState(0)
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return
    const updateWidth = () => setViewerWidth(viewer.clientWidth)
    updateWidth()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(updateWidth)
    observer.observe(viewer)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    let active = true
    setPdf(null); setError(null); setNativeFallback(false); setRendering(true)
    const loadingTask = pdfjsLib.getDocument({ url: originalUrl })
    void loadingTask.promise.then((loaded) => { if (active) setPdf(loaded) }).catch((reason: unknown) => {
      if (!active || isPdfCancellation(reason)) return
      const message = reason instanceof Error ? reason.message : 'PDF повреждён'
      setError(message); setNativeFallback(true); setRendering(false)
    })
    return () => { active = false; void loadingTask.destroy().catch(() => undefined) }
  }, [originalUrl])
  useEffect(() => {
    if (!pdf || !canvasRef.current || !sheetRef.current) return
    let active = true
    let renderTask: pdfjsLib.RenderTask | null = null
    let page: pdfjsLib.PDFPageProxy | null = null
    setRendering(true)

    const renderPage = async () => {
      try {
        page = await pdf.getPage(Math.max(1, Math.min(pageNumber, pdf.numPages)))
        const baseViewport = page.getViewport({ scale: 1 })
        const availableWidth = viewerWidth || viewerRef.current?.clientWidth || sheetRef.current?.parentElement?.clientWidth || 720
        // Fit every page to the visible viewer width. The previous minimum
        // scale could make a page wider than a narrow left column, which
        // forced horizontal scrolling and clipped the document on tablets.
        const targetWidth = Math.max(160, availableWidth - 4)
        const scale = Math.min(1.35, Math.max(0.1, targetWidth / baseViewport.width))
        const viewport = page.getViewport({ scale })
        const canvas = canvasRef.current
        if (!canvas || !active) return
        const ratio = window.devicePixelRatio || 1
        canvas.width = Math.ceil(viewport.width * ratio); canvas.height = Math.ceil(viewport.height * ratio)
        canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`
        sheetRef.current!.style.width = `${viewport.width}px`; sheetRef.current!.style.height = `${viewport.height}px`
        const context = canvas.getContext('2d')
        if (!context) throw new Error('Canvas недоступен')
        setItems([])
        renderTask = page.render({ canvasContext: context, viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined })
        await renderTask.promise
        if (!active) return

        // The canvas is the source of truth. A malformed or unusual text item
        // must not hide a page that has already rendered successfully.
        setRendering(false)
        try {
          const content = await page.getTextContent()
          const mapped = content.items.flatMap((item) => {
            if (!('str' in item) || typeof item.str !== 'string' || !item.str) return []
            if (!('transform' in item) || !Array.isArray(item.transform) || item.transform.length < 6) return []
            const tx = pdfjsLib.Util.transform(viewport.transform, item.transform as number[])
            const height = Math.max(5, Math.hypot(tx[2], tx[3]))
            const rawWidth = 'width' in item && typeof item.width === 'number' ? item.width : 0
            return [{ str: item.str, left: tx[4], top: tx[5] - height, width: Math.max(1, rawWidth * viewport.scale), height }]
          })
          if (active) setItems(mapped)
        } catch (reason: unknown) {
          if (active && !isPdfCancellation(reason)) setItems([])
        }
      } catch (reason: unknown) {
        if (!active || isPdfCancellation(reason)) return
        const message = reason instanceof Error ? reason.message : 'Не удалось отобразить страницу'
        setError(message); setNativeFallback(true); setRendering(false)
      }
    }
    void renderPage()
    return () => { active = false; renderTask?.cancel(); page?.cleanup() }
  }, [pdf, pageNumber, viewerWidth])
  useEffect(() => {
    const registry = (globalThis.CSS as unknown as { highlights?: HighlightRegistryLike } | undefined)?.highlights
    registry?.delete('document-source-range')
    if (!selectedSource) return
    if (isCalculation(selectedSource)) { onMatch('calculation'); return }
    const sourcePage = typeof selectedSource?.locator.page === 'number' ? selectedSource.locator.page : null
    if (sourcePage !== null && sourcePage !== pageNumber) { onMatch('page_only'); return }
    if (selectedSource?.locator.ocr === true && sourcePage === pageNumber) {
      const boxes = pdfOcrWordBoxes(selectedSource)
      onMatch(boxes.length > 0 ? 'exact' : sourcePage !== null ? 'page_only' : 'not_found')
      const target = boxes.length
        ? sheetRef.current?.querySelector('.pdf-ocr-highlight-box')
        : sheetRef.current
      navigateToSource(target)
      return
    }
    const query = sourceQuery(selectedSource)
    if (!items.length) { onMatch(sourcePage !== null ? 'page_only' : 'not_found'); return }
    const starts: number[] = []
    let joined = ''
    items.forEach((item, index) => {
      starts.push(joined.length)
      joined += `${index ? ' ' : ''}${item.str}`
    })
    const match = normalizedRange(joined, query)
    if (!match) {
      onMatch(sourcePage !== null ? 'page_only' : 'not_found')
      if (sourcePage !== null) navigateToSource(sheetRef.current)
      return
    }
    const ranges: Range[] = []
    let target: HTMLSpanElement | null = null
    items.forEach((item, index) => {
      const start = starts[index] + (index ? 1 : 0)
      const end = start + item.str.length
      const from = Math.max(match.start, start)
      const to = Math.min(match.end, end)
      if (to <= from) return
      const span = textSpansRef.current.get(index)
      const textNode = span?.firstChild
      if (!span || !textNode || textNode.nodeType !== Node.TEXT_NODE) return
      const range = document.createRange()
      range.setStart(textNode, from - start)
      range.setEnd(textNode, to - start)
      ranges.push(range)
      target ??= span
    })
    const exact = installTextHighlight('document-source-range', ranges)
    onMatch(exact ? 'exact' : ranges.length ? 'approximate' : sourcePage !== null ? 'page_only' : 'not_found')
    navigateToSource(target || sheetRef.current)
    return () => { registry?.delete('document-source-range') }
  }, [items, pageNumber, selectedSource, onMatch])
  if (nativeFallback) return <div className="pdf-native-fallback">
    <div className="pdf-native-fallback-note">
      <strong>Встроенный просмотр PDF.js недоступен</strong>
      <span>Показываю оригинал через просмотрщик браузера{error ? ` · ${error}` : ''}.</span>
    </div>
    <iframe title="Оригинальный PDF-документ" src={`${originalUrl}#page=${Math.max(1, pageNumber)}`} />
  </div>
  if (error) return <div className="preview-inline-error">{error}</div>
  const query = sourceQuery(selectedSource)
  const joinedItems = items.map((item) => item.str).join(' ')
  const textMatch = normalizedRange(joinedItems, query)
  const matchIndexes = new Set<number>()
  if (textMatch) {
    let cursor = 0
    items.forEach((item, index) => {
      const start = cursor + (index ? 1 : 0)
      const end = start + item.str.length
      if (end > textMatch.start && start < textMatch.end) matchIndexes.add(index)
      cursor = end
    })
  }
  const hasCssHighlights = Boolean((globalThis.CSS as unknown as { highlights?: HighlightRegistryLike } | undefined)?.highlights && (globalThis as typeof globalThis & { Highlight?: HighlightConstructorLike }).Highlight)
  return <div className="pdf-original-page-wrap" ref={viewerRef}>
    {rendering && <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Рендерю страницу {pageNumber}…</div>}
    <div className="pdf-page-sheet" ref={sheetRef}>
      <canvas ref={canvasRef} />
      {selectedSource?.locator.ocr === true && selectedSource.locator.page === pageNumber && <div className="pdf-ocr-highlight-layer" aria-hidden="true">
        {pdfOcrWordBoxes(selectedSource).map((box) => <span className="pdf-ocr-highlight-box" key={box.key} style={{ left: `${box.left}%`, top: `${box.top}%`, width: `${box.width}%`, height: `${box.height}%` }} />)}
      </div>}
      <div className="pdf-text-layer" aria-hidden="true">
        {items.map((item, index) => {
          const match = matchIndexes.has(index)
          return <span ref={(node) => { if (node) textSpansRef.current.set(index, node); else textSpansRef.current.delete(index) }} key={`${item.left}-${item.top}-${index}`} className={`${match ? 'pdf-text-match' : ''}${match && !hasCssHighlights ? ' pdf-text-highlight-fallback' : ''}`} style={{ left: item.left, top: item.top, width: item.width, height: item.height, fontSize: item.height }}>{item.str}</span>
        })}
      </div>
    </div>
  </div>
}

function DocxOriginalViewer({ originalUrl, selectedSource, onMatch, onError }: { originalUrl: string; selectedSource: ViewerSource | null; onMatch: (value: MatchQuality) => void; onError: (message: string) => void }) {
  // docx-preview owns the contents of this host and mutates it imperatively.
  // Keep it separate from React children so replaceChildren() cannot remove
  // React-managed loading or error nodes.
  const containerRef = useRef<HTMLDivElement>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    let resizeObserver: ResizeObserver | null = null
    const controller = new AbortController()
    setLoading(true); setError(null)
    const container = containerRef.current
    if (!container) return
    container.replaceChildren()
    // Render into a detached host first. docx-preview mutates its host
    // imperatively; using the live React node allowed a stale render from a
    // previous document to race with a new one and call removeChild on nodes
    // that React had already replaced.
    const renderHost = document.createElement('div')
    void fetch(originalUrl, { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error('DOCX недоступен')
      const blob = await response.blob()
      await renderAsync(blob, renderHost, undefined, { className: 'docx-preview', breakPages: true, inWrapper: true })
      if (!active) return
      container.replaceChildren(...Array.from(renderHost.childNodes))
      const fitPages = () => {
        const pages = Array.from(container.querySelectorAll<HTMLElement>('.docx-preview-wrapper > section.docx-preview'))
        if (!pages.length || !container.clientWidth) return
        const pageWidth = Math.max(...pages.map((page) => page.offsetWidth))
        const availableWidth = Math.max(1, container.clientWidth - 4)
        const scale = Math.min(1, Math.max(.35, availableWidth / pageWidth))
        pages.forEach((page) => { page.style.zoom = String(scale) })
      }
      resizeObserver = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(fitPages) : null
      resizeObserver?.observe(container)
      window.requestAnimationFrame(fitPages)
      setLoading(false)
    }).catch((reason: unknown) => {
      if (!active || (reason instanceof DOMException && reason.name === 'AbortError')) return
      const message = reason instanceof Error ? reason.message : 'DOCX повреждён'
      setError(message); onError(message); setLoading(false)
    })
    return () => { active = false; controller.abort(); resizeObserver?.disconnect(); renderHost.replaceChildren() }
  }, [originalUrl])
  useEffect(() => {
    const container = containerRef.current
    const registry = (globalThis.CSS as unknown as { highlights?: HighlightRegistryLike } | undefined)?.highlights
    registry?.delete('document-source-range')
    if (!container) return
    container.querySelectorAll<HTMLElement>('.source-match').forEach((element) => element.classList.remove('source-match'))
    const elements = Array.from(container.querySelectorAll<HTMLElement>('p, h1, h2, h3, h4, h5, h6, td, th, li'))
    if (!selectedSource || loading) return
    if (isCalculation(selectedSource)) { onMatch('calculation'); return }
    const query = sourceQuery(selectedSource)
    const paragraphElements = elements.filter((element) => element.matches('p, h1, h2, h3, h4, h5, h6'))
    const tableIndex = typeof selectedSource.locator.table === 'number' ? selectedSource.locator.table - 1 : -1
    const rowIndex = typeof selectedSource.locator.row === 'number' ? selectedSource.locator.row - 1 : -1
    const tableRow = tableIndex >= 0 && rowIndex >= 0
      ? container.querySelectorAll<HTMLElement>('table')[tableIndex]?.querySelectorAll<HTMLElement>('tr')[rowIndex]
      : undefined
    const paragraph = typeof selectedSource.locator.paragraph === 'number'
      ? paragraphElements[selectedSource.locator.paragraph - 1]
      : undefined
    const preferredElements = tableRow
      ? [tableRow, ...Array.from(tableRow.querySelectorAll<HTMLElement>('td, th'))]
      : paragraph ? [paragraph] : []
    const searchOrder = [...preferredElements, ...elements.filter((element) => !preferredElements.includes(element))]
    let target: HTMLElement | undefined
    let ranges: Range[] = []
    for (const element of searchOrder) {
      ranges = rangesWithinElement(element, query)
      if (ranges.length) { target = element; break }
    }
    let exact = Boolean(target && installTextHighlight('document-source-range', ranges))
    if (!target) target = tableRow || paragraph
    if (!exact && target) target.classList.add('source-match')
    const anchored = typeof selectedSource.locator.paragraph === 'number' || typeof selectedSource.locator.row === 'number'
    onMatch(exact ? 'exact' : target ? 'approximate' : anchored ? 'page_only' : 'not_found')
    target?.classList.add('source-match')
    navigateToSource(target)
    return () => { registry?.delete('document-source-range') }
  }, [loading, selectedSource, onMatch])
  if (error) return <div className="preview-inline-error">{error}</div>
  return <div className="docx-original-viewer">
    <div className="docx-render-host" ref={containerRef} />
    {loading && <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Рендерю оригинал DOCX…</div>}
  </div>
}

function CsvOriginalViewer({ preview, selectedSource, onMatch, onError }: { preview: DocumentPreview; selectedSource: ViewerSource | null; onMatch: (value: MatchQuality) => void; onError: (message: string) => void }) {
  const tableHelpTargetRef = useAppHelpTargetRef('document.table.controls')
  const [table, setTable] = useState<TablePreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [queryError, setQueryError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [sortColumn, setSortColumn] = useState<number | null>(null)
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc')
  const [appliedFilter, setAppliedFilter] = useState<TableFilter | null>(null)
  const [filterColumn, setFilterColumn] = useState(0)
  const [filterKind, setFilterKind] = useState<TableFilterKind>('text')
  const [filterOperator, setFilterOperator] = useState<TableFilter['operator']>('contains')
  const [filterValue, setFilterValue] = useState('')
  const [calculationColumn, setCalculationColumn] = useState(0)
  const [calculation, setCalculation] = useState<TableCalculation | null>(null)
  const [calculationLoading, setCalculationLoading] = useState(false)
  const [calculationError, setCalculationError] = useState<string | null>(null)
  const tableRef = useRef<HTMLDivElement>(null)
  const tableScrollRef = useRef<HTMLDivElement>(null)
  const [tableScrollTop, setTableScrollTop] = useState(0)
  const [tableViewportHeight, setTableViewportHeight] = useState(360)
  const requestRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  const calculationRequestRef = useRef(0)
  const calculationAbortRef = useRef<AbortController | null>(null)
  const navigatedSourceRef = useRef<string | null>(null)
  const queryRef = useRef<{ sheet: string | null; sortColumn: number | null; sortDirection: 'asc' | 'desc'; filter: TableFilter | null }>({
    sheet: null,
    sortColumn: null,
    sortDirection: 'asc',
    filter: null,
  })
  const autoSelectedCalculationColumnRef = useRef(false)
  const virtualizedRows = Boolean(table && table.rows.length > TABLE_VIRTUALIZE_AFTER)
  const virtualRowStart = virtualizedRows
    ? Math.max(0, Math.floor(Math.max(0, tableScrollTop - TABLE_HEADER_HEIGHT) / TABLE_ROW_HEIGHT) - TABLE_OVERSCAN_ROWS)
    : 0
  const virtualRowEnd = virtualizedRows
    ? Math.min(table?.rows.length ?? 0, Math.ceil((tableScrollTop + tableViewportHeight - TABLE_HEADER_HEIGHT) / TABLE_ROW_HEIGHT) + TABLE_OVERSCAN_ROWS)
    : table?.rows.length ?? 0
  const visibleTableRows = table?.rows.slice(virtualRowStart, virtualRowEnd) ?? []

  useEffect(() => {
    const element = tableScrollRef.current
    if (!element || !table) return
    const updateHeight = () => setTableViewportHeight(element.clientHeight || 360)
    updateHeight()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(updateHeight)
    observer.observe(element)
    return () => observer.disconnect()
  }, [Boolean(table)])

  const load = async (
    offset: number,
    append: boolean,
    override: Partial<typeof queryRef.current> = {},
    focusRow: number | null = null,
  ) => {
    const query = { ...queryRef.current, ...override }
    if (!append) queryRef.current = query
    if (!append && tableScrollRef.current) {
      tableScrollRef.current.scrollTop = 0
      setTableScrollTop(0)
    }
    const requestId = ++requestRef.current
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setLoading(true)
    setError(null)
    setQueryError(null)
    try {
      const params = new URLSearchParams({ offset: String(offset), limit: String(CSV_PAGE_SIZE) })
      if (query.sheet) params.set('sheet', query.sheet)
      if (query.sortColumn !== null) {
        params.set('sort_column', String(query.sortColumn))
        params.set('sort_direction', query.sortDirection)
      }
      if (query.filter) {
        params.set('filter_column', String(query.filter.column_index))
        params.set('filter_kind', query.filter.kind)
        params.set('filter_operator', query.filter.operator)
        if (query.filter.kind !== 'empty') params.set('filter_value', query.filter.value)
      }
      if (focusRow !== null) params.set('focus_row', String(focusRow))
      const response = await fetch(`${API}/documents/${preview.document_id}/preview/table?${params}`, { signal: controller.signal })
      const payload = await response.json().catch(() => ({})) as { detail?: string }
      if (!response.ok) throw Object.assign(new Error(payload.detail || 'Таблица недоступна'), { status: response.status })
      const next = payload as TablePreview
      if (requestId !== requestRef.current) return
      if (focusRow !== null && next.focus_row_visible === false && query.filter) {
        const resetQuery = { sheet: query.sheet, sortColumn: null, sortDirection: 'asc' as const, filter: null }
        queryRef.current = resetQuery
        setSortColumn(null)
        setSortDirection('asc')
        setAppliedFilter(null)
        setFilterValue('')
        setNotice('Фильтр скрывал этот источник. Фильтр сброшен, чтобы показать исходную строку.')
        void load(Math.max(0, focusRow - 2), false, resetQuery, focusRow)
        return
      }
      setTable((current) => append && current
        ? { ...next, rows: [...current.rows, ...next.rows], offset: current.offset, limit: next.limit }
        : next)
    } catch (reason) {
      if (requestId !== requestRef.current || (reason instanceof DOMException && reason.name === 'AbortError')) return
      const message = reason instanceof Error ? reason.message : 'Не удалось загрузить таблицу'
      setError(message)
      if (!table) onError(message)
      else setQueryError(message)
    } finally {
      if (requestId === requestRef.current) setLoading(false)
    }
  }

  useEffect(() => {
    queryRef.current = { sheet: null, sortColumn: null, sortDirection: 'asc', filter: null }
    autoSelectedCalculationColumnRef.current = false
    navigatedSourceRef.current = null
    abortRef.current?.abort()
    calculationRequestRef.current += 1
    calculationAbortRef.current?.abort()
    setTable(null)
    setLoading(true)
    setError(null)
    setQueryError(null)
    setNotice(null)
    setSortColumn(null)
    setSortDirection('asc')
    setAppliedFilter(null)
    setFilterColumn(0)
    setFilterKind('text')
    setFilterOperator('contains')
    setFilterValue('')
    setCalculationColumn(0)
    setCalculation(null)
    setCalculationLoading(false)
    setCalculationError(null)
    void load(0, false)
    return () => {
      requestRef.current += 1
      abortRef.current?.abort()
      calculationRequestRef.current += 1
      calculationAbortRef.current?.abort()
    }
  }, [preview.document_id])

  useEffect(() => {
    if (!table || autoSelectedCalculationColumnRef.current) return
    autoSelectedCalculationColumnRef.current = true
    const firstNumeric = table.column_kinds.indexOf('number')
    if (firstNumeric >= 0) setCalculationColumn(firstNumeric)
  }, [table])

  useEffect(() => {
    if (!table || !selectedSource) return
    const newSource = navigatedSourceRef.current !== selectedSource.id
    const start = typeof selectedSource.locator.row_start === 'number' ? selectedSource.locator.row_start : null
    const requestedSheet = typeof selectedSource.locator.sheet === 'string' ? selectedSource.locator.sheet : null
    const derived = isCalculation(selectedSource)
    if (requestedSheet && table.sheet !== requestedSheet) {
      if (newSource) {
        navigatedSourceRef.current = selectedSource.id
        const resetQuery = { sheet: requestedSheet, sortColumn: null, sortDirection: 'asc' as const, filter: null }
        queryRef.current = resetQuery
        setSortColumn(null)
        setSortDirection('asc')
        setAppliedFilter(null)
        setFilterValue('')
        void load(0, false, resetQuery, start)
      }
      return
    }
    if (newSource) navigatedSourceRef.current = selectedSource.id
    if (start === null) { onMatch(derived ? 'calculation' : 'not_found'); return }
    const selectedColumn = typeof selectedSource.locator.column === 'string' ? selectedSource.locator.column : null
    const selectedColumnIndex = typeof selectedSource.locator.column_index === 'number'
      ? selectedSource.locator.column_index
      : selectedColumn ? table.columns.indexOf(selectedColumn) : -1
    if (start === 1) {
      const header = selectedColumnIndex >= 0
        ? tableRef.current?.querySelector(`thead [data-column-index="${selectedColumnIndex}"]`)
        : null
      onMatch(derived ? 'calculation' : header ? 'exact' : 'not_found')
      if (header) navigateToSource(header)
      return
    }
    const end = typeof selectedSource.locator.row_end === 'number' ? selectedSource.locator.row_end : start
    const loaded = table.rows.some((row) => row.number >= start && row.number <= end)
    if (!loaded) {
      if (newSource) void load(0, false, {}, start)
      else onMatch(selectedSource.locator.sheet ? 'page_only' : 'not_found')
      return
    }
    const target = table.rows.find((row) => row.number >= start && row.number <= end)
    const targetIndex = target ? table.rows.indexOf(target) : -1
    if (targetIndex >= 0 && virtualizedRows && (targetIndex < virtualRowStart || targetIndex >= virtualRowEnd)) {
      if (tableScrollRef.current) tableScrollRef.current.scrollTo({ top: targetIndex * TABLE_ROW_HEIGHT, behavior: 'auto' })
      return
    }
    const exact = Boolean(target && !derived)
    onMatch(derived ? 'calculation' : exact ? 'exact' : selectedSource.locator.sheet ? 'page_only' : 'not_found')
    if (target) navigateToSource(tableRef.current?.querySelector(`[data-row-number="${target.number}"]`))
  }, [table, selectedSource, onMatch, virtualRowStart, virtualRowEnd, virtualizedRows])

  if (error && !table) return <div className="preview-inline-error">{error}</div>
  if (!table) return <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Загружаю оригинальную таблицу…</div>

  const operators: Record<TableFilterKind, Array<{ value: TableFilter['operator']; label: string }>> = {
    text: [{ value: 'contains', label: 'содержит' }, { value: 'equals', label: 'совпадает с' }],
    number: [
      { value: 'equals', label: '=' }, { value: 'gt', label: '>' }, { value: 'gte', label: '≥' },
      { value: 'lt', label: '<' }, { value: 'lte', label: '≤' },
    ],
    empty: [{ value: 'is_empty', label: 'пусто' }, { value: 'is_not_empty', label: 'не пусто' }],
  }
  const changeFilterKind = (kind: TableFilterKind) => {
    setFilterKind(kind)
    setFilterOperator(operators[kind][0].value)
    if (kind === 'empty') setFilterValue('')
  }
  const applyFilter = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const filter: TableFilter = {
      column_index: filterColumn,
      kind: filterKind,
      operator: filterOperator,
      value: filterKind === 'empty' ? '' : filterValue.trim(),
    }
    setAppliedFilter(filter)
    setNotice(null)
    setCalculation(null)
    void load(0, false, { filter })
  }
  const clearFilter = () => {
    setAppliedFilter(null)
    setFilterValue('')
    setNotice(null)
    setCalculation(null)
    void load(0, false, { filter: null })
  }
  const changeSheet = (sheet: string) => {
    const nextQuery = { sheet, sortColumn: null, sortDirection: 'asc' as const, filter: null }
    queryRef.current = nextQuery
    setSortColumn(null)
    setSortDirection('asc')
    setAppliedFilter(null)
    setFilterValue('')
    setCalculation(null)
    setNotice(null)
    void load(0, false, nextQuery)
  }
  const toggleSort = (columnIndex: number) => {
    const direction = sortColumn === columnIndex && sortDirection === 'asc' ? 'desc' : 'asc'
    setSortColumn(columnIndex)
    setSortDirection(direction)
    setCalculation(null)
    void load(0, false, { sortColumn: columnIndex, sortDirection: direction })
  }
  const clearSort = () => {
    setSortColumn(null)
    setSortDirection('asc')
    setCalculation(null)
    void load(0, false, { sortColumn: null, sortDirection: 'asc' })
  }
  const calculate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const requestId = ++calculationRequestRef.current
    calculationAbortRef.current?.abort()
    const controller = new AbortController()
    calculationAbortRef.current = controller
    setCalculationLoading(true)
    setCalculationError(null)
    try {
      const response = await fetch(`${API}/documents/${preview.document_id}/preview/table/calculations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sheet: table.sheet, column_index: calculationColumn, filter: appliedFilter }),
        signal: controller.signal,
      })
      const payload = await response.json().catch(() => ({})) as TableCalculation & { detail?: string }
      if (!response.ok) throw new Error(payload.detail || 'Не удалось выполнить расчёт')
      if (requestId !== calculationRequestRef.current) return
      setCalculation(payload)
    } catch (reason) {
      if (requestId !== calculationRequestRef.current || (reason instanceof DOMException && reason.name === 'AbortError')) return
      setCalculationError(reason instanceof Error ? reason.message : 'Не удалось выполнить расчёт')
    } finally {
      if (requestId === calculationRequestRef.current) setCalculationLoading(false)
    }
  }
  const renderAggregate = (title: string, aggregate: TableAggregate, source: TableCalculation['document_source']) => (
    <section className="csv-calculation-scope">
      <h4>{title}</h4>
      <dl>
        <div><dt>Строк</dt><dd>{aggregate.count.toLocaleString('ru-RU')}</dd></div>
        <div><dt>Непустых</dt><dd>{aggregate.non_empty_count.toLocaleString('ru-RU')}</dd></div>
        <div><dt>Числовых</dt><dd>{aggregate.numeric_count.toLocaleString('ru-RU')}</dd></div>
        <div><dt>Нечисловых</dt><dd>{aggregate.nonnumeric_count.toLocaleString('ru-RU')}</dd></div>
        <div><dt>Сумма</dt><dd>{formatTableDecimal(aggregate.sum)}</dd></div>
        <div><dt>Среднее</dt><dd>{formatTableDecimal(aggregate.average)}</dd></div>
        <div><dt>Минимум</dt><dd>{formatTableDecimal(aggregate.minimum)}</dd></div>
        <div><dt>Максимум</dt><dd>{formatTableDecimal(aggregate.maximum)}</dd></div>
      </dl>
      {aggregate.formula_count > 0 && <p className="csv-formula-summary">Формул: {aggregate.formula_count}; без сохранённого результата: {aggregate.formula_cache_missing_count}. Приложение формулы не вычисляло.</p>}
      <details className="csv-calculation-source">
        <summary>Проверяемый источник · {source.id.slice(0, 8)}</summary>
        <pre>{source.text}</pre>
      </details>
    </section>
  )
  const filterColumnName = appliedFilter ? table.columns[appliedFilter.column_index] : null

  return <div className="csv-original-viewer" ref={tableRef}>
    <div ref={tableHelpTargetRef} data-help-target="document.table.controls" className="csv-sheet-toolbar csv-table-toolbar">
      {table.available_sheets.length > 1 && <div className="csv-sheet-picker">
        <label htmlFor={`csv-sheet-${preview.document_id}`}>Лист</label>
        <select id={`csv-sheet-${preview.document_id}`} aria-label="Лист исходного файла" value={table.sheet || ''} onChange={(event) => changeSheet(event.target.value)}>
          {table.available_sheets.map((sheet) => <option key={sheet} value={sheet}>{sheet}</option>)}
        </select>
      </div>}
      <span className="csv-table-guidance">Сортировка: нажмите на заголовок столбца</span>
      {sortColumn !== null && <button type="button" className="csv-tool-reset" onClick={clearSort}>Сбросить сортировку</button>}
    </div>
    <form className="csv-filter-controls" aria-label="Фильтр таблицы" onSubmit={applyFilter}>
      <div className="csv-filter-field"><label htmlFor={`csv-filter-column-${preview.document_id}`}>Столбец</label>
        <select id={`csv-filter-column-${preview.document_id}`} aria-label="Столбец фильтра" value={filterColumn} onChange={(event) => setFilterColumn(Number(event.target.value))}>
          {table.columns.map((column, index) => <option key={index} value={index}>{column}</option>)}
        </select>
      </div>
      <div className="csv-filter-field"><label htmlFor={`csv-filter-kind-${preview.document_id}`}>Тип условия</label>
        <select id={`csv-filter-kind-${preview.document_id}`} aria-label="Тип фильтра" value={filterKind} onChange={(event) => changeFilterKind(event.target.value as TableFilterKind)}>
          <option value="text">Текст</option><option value="number">Число</option><option value="empty">Пустое значение</option>
        </select>
      </div>
      <div className="csv-filter-field"><label htmlFor={`csv-filter-operator-${preview.document_id}`}>Условие</label>
        <select id={`csv-filter-operator-${preview.document_id}`} aria-label="Условие фильтра" value={filterOperator} onChange={(event) => setFilterOperator(event.target.value as TableFilter['operator'])}>
          {operators[filterKind].map((operator) => <option key={operator.value} value={operator.value}>{operator.label}</option>)}
        </select>
      </div>
      {filterKind !== 'empty' && <div className="csv-filter-field csv-filter-value"><label htmlFor={`csv-filter-value-${preview.document_id}`}>{filterKind === 'number' ? 'Значение' : 'Текст'}</label>
        <input id={`csv-filter-value-${preview.document_id}`} aria-label="Значение фильтра" value={filterValue} onChange={(event) => setFilterValue(event.target.value)} inputMode={filterKind === 'number' ? 'decimal' : 'search'} maxLength={256} required placeholder={filterKind === 'number' ? 'Например, 12,50' : 'Введите фрагмент текста'} />
      </div>}
      <div className="csv-filter-actions">
        <button type="submit" className="button button-dark" disabled={loading}>{loading ? 'Применяю…' : 'Применить'}</button>
        {appliedFilter && <button type="button" className="csv-tool-reset" onClick={clearFilter} disabled={loading}>Сбросить</button>}
      </div>
    </form>
    {appliedFilter && <div className="csv-filter-summary" role="status">
      <Filter size={13} aria-hidden="true" /> Фильтр: {filterColumnName} · {operators[appliedFilter.kind].find((item) => item.value === appliedFilter.operator)?.label}{appliedFilter.value ? ` «${appliedFilter.value}»` : ''}
    </div>}
    {notice && <div className="csv-table-notice" role="status">{notice}</div>}
    {queryError && <div className="csv-table-error" role="alert">{queryError}</div>}
    {table.formula_policy !== 'not_applicable' && <p className="csv-formula-policy" role="note">
      {table.formula_policy === 'detected'
        ? 'Формулы XLSX не выполняются. Показаны сохранённые в файле значения; пустая ячейка формулы отмечается отдельно.'
        : 'Формулы XLS не выполняются. Формат позволяет читать сохранённые значения, но не раскрывает формулы и наличие их кэша.'}
    </p>}
    <details className="csv-calculation-panel">
      <summary><Calculator size={14} aria-hidden="true" /> Проверяемые расчёты <span>выполняются приложением</span></summary>
      <form className="csv-calculation-controls" onSubmit={calculate}>
        <label htmlFor={`csv-calc-column-${preview.document_id}`}>Столбец</label>
        <select id={`csv-calc-column-${preview.document_id}`} aria-label="Столбец для расчёта" value={calculationColumn} onChange={(event) => { setCalculationColumn(Number(event.target.value)); setCalculation(null) }}>
          {table.columns.map((column, index) => <option key={index} value={index}>{column}</option>)}
        </select>
        <button type="submit" className="button button-light" disabled={calculationLoading}>{calculationLoading ? <><LoaderCircle className="spin" size={13} /> Считаю…</> : 'Рассчитать'}</button>
      </form>
      <p className="csv-calculation-rule">Сумма, минимум и максимум используют точную десятичную арифметику. Среднее округляется до 2 знаков по правилу ROUND_HALF_UP. Нечисловые значения исключаются из числовых итогов.</p>
      {calculationError && <p className="csv-table-error" role="alert">{calculationError}</p>}
      {calculation && <>
        <p className="csv-calculation-context">{calculation.sheet ? `Лист «${calculation.sheet}» · ` : ''}Столбец «{calculation.column}» · {calculation.filter ? `фильтр по столбцу «${table.columns[calculation.filter.column_index]}»` : 'без фильтра'}</p>
        <div className="csv-calculation-grid">
          {renderAggregate('Весь документ', calculation.document, calculation.document_source)}
          {renderAggregate('Текущий фильтр', calculation.filtered, calculation.filtered_source)}
        </div>
      </>}
    </details>
    <div
      className={`csv-table-scroll${virtualizedRows ? ' is-virtualized' : ''}`}
      ref={tableScrollRef}
      onScroll={(event) => setTableScrollTop(event.currentTarget.scrollTop)}
    >
      <table className="original-csv-table"><thead><tr><th scope="col">№</th>{table.columns.map((column, index) => {
        const activeSort = sortColumn === index
        const nextDirection = activeSort && sortDirection === 'asc' ? 'по убыванию' : 'по возрастанию'
        return <th scope="col" aria-sort={activeSort ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'} data-column-index={index} className={selectedSource && selectedSource.locator.row_start === 1 && selectedSource.locator.column_index === index ? 'source-cell-match' : ''} key={index}>
          <button type="button" className="csv-sort-button" onClick={() => toggleSort(index)} aria-label={`Сортировать по столбцу ${column} ${nextDirection}`} title={`Сортировать ${nextDirection}`}>
            {selectedSource && selectedSource.locator.row_start === 1 && selectedSource.locator.column_index === index
              ? renderCellMatch(column, null, null)
              : column}
            {activeSort ? (sortDirection === 'asc' ? <ArrowUp size={12} aria-hidden="true" /> : <ArrowDown size={12} aria-hidden="true" />) : <ArrowUpDown size={12} aria-hidden="true" />}
          </button>
        </th>
      })}</tr></thead>
        <tbody>
          {virtualizedRows && virtualRowStart > 0 && <tr className="csv-virtual-spacer" aria-hidden="true"><td colSpan={table.columns.length + 1} style={{ height: virtualRowStart * TABLE_ROW_HEIGHT }} /></tr>}
          {visibleTableRows.map((row) => {
          const start = typeof selectedSource?.locator.row_start === 'number' ? selectedSource.locator.row_start : null
          const end = typeof selectedSource?.locator.row_end === 'number' ? selectedSource.locator.row_end : start
          const inRange = start !== null && row.number >= start && row.number <= (end ?? start)
          return <tr data-row-number={row.number} className={inRange ? 'source-row-match' : ''} key={row.number}><td className="csv-row-number">{row.number}</td>{row.cells.map((cell, index) => {
            const formula = row.formula_cells.find((item) => item.column_index === index)
            const selectedColumn = typeof selectedSource?.locator.column_index === 'number'
              ? selectedSource.locator.column_index
              : typeof selectedSource?.locator.column === 'string' ? table.columns.indexOf(selectedSource.locator.column) : -1
            const selectedCell = inRange && selectedColumn === index
            const formulaLabel = formula
              ? formula.has_cached_value ? `Формула ${formula.formula}; отображено сохранённое значение.` : `Формула ${formula.formula}; сохранённого результата нет.`
              : undefined
            return <td className={`${selectedCell ? 'source-cell-match' : ''}${formula && !formula.has_cached_value ? ' csv-formula-without-cache' : ''}`} key={`${row.number}-${index}`} title={formulaLabel ? `${cell} · ${formulaLabel}` : cell} aria-label={formulaLabel || cell}>
              {selectedCell ? renderCellMatch(cell, null, null) : cell || (formula && !formula.has_cached_value ? 'Нет результата' : '—')}
              {formula && <span className="csv-formula-indicator" aria-hidden="true">fx</span>}
            </td>
          })}</tr>
          })}
          {virtualizedRows && virtualRowEnd < table.rows.length && <tr className="csv-virtual-spacer" aria-hidden="true"><td colSpan={table.columns.length + 1} style={{ height: (table.rows.length - virtualRowEnd) * TABLE_ROW_HEIGHT }} /></tr>}
        </tbody>
      </table>
    </div>
    <div className="csv-table-footer"><span>{table.sheet ? `Лист «${table.sheet}» · ` : ''}Показано {table.rows.length.toLocaleString('ru-RU')} из {table.filtered_rows.toLocaleString('ru-RU')} строк{appliedFilter ? ` · всего в таблице ${table.total_rows.toLocaleString('ru-RU')}` : ''}</span>{table.rows.length < table.filtered_rows && <button type="button" className="button button-light" onClick={() => void load(table.offset + table.rows.length, true)} disabled={loading}>{loading ? 'Загружаю…' : 'Показать ещё'}</button>}</div>
  </div>
}

function formatTableDecimal(value: string | null): string {
  if (value === null) return '—'
  const [integer, fraction] = value.split('.')
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0')
  return fraction === undefined ? grouped : `${grouped},${fraction}`
}
function mappedStructuredLocatorMatches(blockLocator: Record<string, unknown>, sourceLocator: Record<string, unknown>): boolean {
  const linked = Array.isArray(blockLocator.source_locators)
    ? blockLocator.source_locators.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
    : []
  return [blockLocator, ...linked].some((candidate) => {
    if (typeof sourceLocator.slide === 'number' && typeof sourceLocator.shape === 'number') {
      return candidate.slide === sourceLocator.slide && candidate.shape === sourceLocator.shape
    }
    if (typeof sourceLocator.chapter === 'number' && typeof sourceLocator.path === 'string') {
      if (candidate.chapter !== sourceLocator.chapter || candidate.path !== sourceLocator.path) return false
      const sourceStart = sourceLocator.char_start
      const sourceEnd = sourceLocator.char_end
      if (typeof sourceStart === 'number' && typeof sourceEnd === 'number') {
        return candidate.char_start === sourceStart && candidate.char_end === sourceEnd
      }
      if (typeof sourceLocator.element === 'string' && typeof candidate.element === 'string') {
        return candidate.element === sourceLocator.element
      }
      return true
    }
    return false
  })
}

function SourceMapOriginalViewer({ preview, selectedSource, onMatch }: { preview: DocumentPreview; selectedSource: ViewerSource | null; onMatch: (value: MatchQuality) => void }) {
  const viewerRef = useRef<HTMLDivElement>(null)
  const selectedId = selectedSource?.id
  const selectedBlock = selectedSource && !isCalculation(selectedSource)
    ? preview.blocks.find((block) => block.source_id === selectedSource.id) || preview.blocks.find((block) => mappedStructuredLocatorMatches(block.locator, selectedSource.locator))
    : undefined
  const selectedBlockId = selectedBlock?.source_id
  useEffect(() => {
    if (!selectedSource) return
    if (isCalculation(selectedSource)) { onMatch('calculation'); return }
    const target = Array.from(viewerRef.current?.querySelectorAll<HTMLElement>('[data-source-id]') || []).find((element) => element.dataset.sourceId === selectedBlockId)
    const hasAnchor = typeof selectedSource.locator.slide === 'number' || typeof selectedSource.locator.chapter === 'number'
    const exactSearchRange = selectedSource.locator.search_match === true && selectedBlock
      ? normalizedRange(selectedBlock.text, sourceQuery(selectedSource))
      : null
    onMatch(exactSearchRange ? 'exact' : target ? 'approximate' : hasAnchor ? 'page_only' : 'not_found')
    navigateToSource(target || viewerRef.current?.querySelector('.source-map-fallback'))
  }, [preview.blocks, selectedBlockId, selectedId, selectedSource, onMatch])
  return <div className="source-map-original-viewer" ref={viewerRef}>
    {preview.blocks.map((block) => {
      const active = block.source_id === selectedBlockId
      const match = active && selectedSource?.locator.search_match === true ? normalizedRange(block.text, sourceQuery(selectedSource)) : null
      const blockText = match
        ? <>{block.text.slice(0, match.start)}<mark className="document-search-highlight" data-search-match="true">{block.text.slice(match.start, match.end)}</mark>{block.text.slice(match.end)}</>
        : block.text
      return <article className={`preview-block ${active ? 'source-match' : ''}`} data-source-id={block.source_id} key={block.id}>
        <div className="preview-block-meta"><span>{locatorLabel(block)}</span></div>
        <p>{blockText}</p>
      </article>
    })}
    {selectedSource && !isCalculation(selectedSource) && !selectedBlock && <article className="preview-block source-match source-map-fallback" data-source-id={selectedId}>
      <div className="preview-block-meta"><span>Сохранённый фрагмент · {locatorLabel(selectedSource)}</span></div>
      <p>{selectedSource.text}</p>
    </article>}
  </div>
}

export function OriginalDocumentViewer({ document: record, preview, selectedSource, selectedSourceId, originalUrl, pageNumber, onReprocess, reprocessing = false }: OriginalDocumentViewerProps) {
  const ocrHelpTargetRef = useAppHelpTargetRef('document.ocr.settings')
  const [renderError, setRenderError] = useState<string | null>(null)
  const [matchReport, setMatchReport] = useState<{ sourceId: string | null; quality: MatchQuality }>({ sourceId: null, quality: 'not_found' })
  const [retryKey, setRetryKey] = useState(0)
  const [ocrSettingsOpen, setOcrSettingsOpen] = useState(false)
  const [language, setLanguage] = useState<OcrLanguage>('rus+eng')
  const [quality, setQuality] = useState<OcrQuality>('balanced')
  const [pageScope, setPageScope] = useState<'all' | 'selected'>('all')
  const [selectedPages, setSelectedPages] = useState<Set<number>>(new Set())
  const [confirmReprocess, setConfirmReprocess] = useState(false)
  const dialogRef = useRef<HTMLDialogElement>(null)
  const handleMatch = useCallback((quality: MatchQuality) => setMatchReport({ sourceId: selectedSourceId, quality }), [selectedSourceId])
  const matchQuality = matchReport.sourceId === selectedSourceId ? matchReport.quality : 'not_found'
  const renderer = preview.renderer || getDocumentPreviewRenderer(record.file_type)
  const viewerKind = getDocumentViewerKind(record.file_type)
  const rawPageMap = Array.isArray(record.metadata.pdf_page_map) ? record.metadata.pdf_page_map as Record<string, unknown>[] : []
  const rawOcrPageMap = Array.isArray(record.metadata.ocr_page_map) ? record.metadata.ocr_page_map as Record<string, unknown>[] : []
  const ocrPageMap = new Map(rawOcrPageMap.flatMap((item) => typeof item.page === 'number' ? [[item.page, item] as const] : []))
  const pageRows: OcrPageInfo[] = rawPageMap.flatMap((item) => {
    if (typeof item.page !== 'number') return []
    const ocrInfo = ocrPageMap.get(item.page) || {}
    const confidence = typeof item.ocr_confidence === 'number' ? item.ocr_confidence : typeof ocrInfo.confidence === 'number' ? ocrInfo.confidence : null
    return [{
      page: item.page,
      classification: typeof item.classification === 'string' ? item.classification : 'unknown',
      nativeClassification: typeof item.native_classification === 'string' ? item.native_classification : 'unknown',
      ocrResult: typeof item.ocr_result === 'string' ? item.ocr_result : typeof ocrInfo.classification === 'string' ? ocrInfo.classification : 'not_run',
      error: typeof ocrInfo.error === 'string' ? ocrInfo.error : null,
      confidence,
      language: typeof item.ocr_language === 'string' ? item.ocr_language : typeof ocrInfo.language === 'string' ? ocrInfo.language : null,
      dpi: typeof item.ocr_dpi === 'number' ? item.ocr_dpi : typeof ocrInfo.dpi === 'number' ? ocrInfo.dpi : null,
    }]
  })
  const pageSignature = pageRows.map((page) => `${page.page}:${page.classification}:${page.ocrResult}:${page.confidence}`).join('|')
  const warningThreshold = typeof record.metadata.ocr_confidence_warning_threshold === 'number' ? record.metadata.ocr_confidence_warning_threshold : 60
  const maxOcrPages = typeof record.metadata.ocr_max_pages === 'number' && record.metadata.ocr_max_pages > 0 ? record.metadata.ocr_max_pages : 100
  const eligiblePages = pageRows.filter((page) => page.classification !== 'blank' && page.ocrResult !== 'blank' && (
    page.classification === 'ocr' || page.classification === 'ocr_candidate' || page.classification === 'unreadable' ||
    page.nativeClassification === 'ocr_candidate' || page.ocrResult === 'unreadable'
  )).map((page) => page.page)
  const selectablePages = eligiblePages.length ? eligiblePages : pageRows.filter((page) => page.classification !== 'blank').map((page) => page.page)
  const lowConfidencePages = pageRows.filter((page) => page.ocrResult === 'ocr' && page.confidence !== null && page.confidence < warningThreshold)
  const statusCounts = {
    recognized: pageRows.filter((page) => page.classification === 'ocr').length,
    native: pageRows.filter((page) => page.classification === 'native').length,
    blank: pageRows.filter((page) => page.classification === 'blank').length,
    unreadable: pageRows.filter((page) => page.ocrResult === 'unreadable' || page.error === 'page_limit').length,
  }
  const pageStatusLabel = (page: OcrPageInfo) => {
    if (page.error === 'page_limit') return 'Не обработана: лимит страниц'
    if (page.classification === 'blank' || page.ocrResult === 'blank') return 'Пустая страница'
    if (page.classification === 'ocr' && page.confidence !== null) return `Распознана · ${Math.round(page.confidence)} из 100 по эвристике`
    if (page.classification === 'ocr') return 'Распознана'
    if (page.ocrResult === 'unreadable') return 'Не распознана · исходный текст сохранён, если он был'
    if (page.nativeClassification === 'ocr_candidate' || page.classification === 'ocr_candidate') return 'Скан · OCR пока не запускался'
    if (page.classification === 'native') return 'Текстовый слой PDF'
    return 'Текст не найден'
  }
  useEffect(() => {
    const stored = record.metadata.ocr_settings as Record<string, unknown> | undefined
    const savedLanguage = stored?.language
    const savedDpi = stored?.dpi ?? record.metadata.ocr_dpi
    if (savedLanguage === 'rus' || savedLanguage === 'eng' || savedLanguage === 'rus+eng') setLanguage(savedLanguage)
    else if (record.ocr_language === 'rus' || record.ocr_language === 'eng' || record.ocr_language === 'rus+eng') setLanguage(record.ocr_language)
    if (savedDpi === 150) setQuality('fast')
    else if (savedDpi === 300) setQuality('high')
    else setQuality('balanced')
    setPageScope('all')
    setConfirmReprocess(false)
    setOcrSettingsOpen(false)
  }, [record.id, record.active_version])
  useEffect(() => {
    setSelectedPages(new Set(selectablePages))
  // pageSignature changes when an active OCR version replaces its page map.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [record.id, record.active_version, pageSignature])
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (confirmReprocess && !dialog.open) dialog.showModal()
    else if (!confirmReprocess && dialog.open) dialog.close()
  }, [confirmReprocess])
  const submitReprocess = async () => {
    if (!onReprocess) return
    const pages = pageScope === 'all' ? null : [...selectedPages].sort((a, b) => a - b)
    const accepted = await onReprocess({ language, quality, pages })
    if (accepted) setConfirmReprocess(false)
  }
  const openSourcePageReprocess = () => {
    const sourcePage = typeof selectedSource?.locator.page === 'number' ? selectedSource.locator.page : null
    if (sourcePage !== null) {
      setPageScope('selected')
      setSelectedPages(new Set([sourcePage]))
    }
    setOcrSettingsOpen(true)
    setConfirmReprocess(true)
  }
  const content = useMemo(() => {
    const onError = (message: string) => setRenderError(message)
    if (viewerKind === 'pdf') return <PdfOriginalViewer key={retryKey} originalUrl={originalUrl} pageNumber={pageNumber} selectedSource={selectedSource} onMatch={handleMatch} />
    if (viewerKind === 'docx') return <DocxOriginalViewer key={retryKey} originalUrl={originalUrl} selectedSource={selectedSource} onMatch={handleMatch} onError={onError} />
    if (viewerKind === 'csv') return <CsvOriginalViewer key={retryKey} preview={preview} selectedSource={selectedSource} onMatch={handleMatch} onError={onError} />
    if (viewerKind === 'mapped') return <SourceMapOriginalViewer key={retryKey} preview={preview} selectedSource={selectedSource} onMatch={handleMatch} />
    return <TextOriginalViewer key={retryKey} preview={preview} originalUrl={originalUrl} selectedSource={selectedSource} onMatch={handleMatch} onError={onError} />
  }, [handleMatch, originalUrl, pageNumber, preview, record.file_type, renderer, retryKey, selectedSource, viewerKind])
  useEffect(() => { setRenderError(null) }, [preview.document_id, renderer, retryKey])
  return <div className={`original-viewer-body renderer-${renderer}`} data-renderer={renderer} data-selected-source={selectedSourceId || undefined}>
    <div className="preview-toolbar"><span>Оригинал файла{preview.encoding ? ` · ${preview.encoding}` : ''}</span><a href={originalUrl} target="_blank" rel="noreferrer">Открыть исходный файл</a></div>
    {renderer === 'pdf' && <section className="ocr-panel" aria-label="Статус и настройки OCR" data-testid="ocr-panel">
      <div className="ocr-panel-summary">
        <BookOpen size={15} aria-hidden="true" />
        <span>{statusCounts.recognized} OCR · {statusCounts.native} текстовых · {statusCounts.blank} пустых{statusCounts.unreadable ? ` · ${statusCounts.unreadable} требуют внимания` : ''}</span>
        {onReprocess && <button ref={ocrHelpTargetRef} data-help-target="document.ocr.settings" className="ocr-panel-toggle" type="button" aria-expanded={ocrSettingsOpen} onClick={() => setOcrSettingsOpen((open) => !open)}>
          <Settings2 size={14} /> {ocrSettingsOpen ? 'Скрыть настройки' : 'Настроить OCR'} <ChevronDown size={14} className={ocrSettingsOpen ? 'is-open' : ''} />
        </button>}
      </div>
      {lowConfidencePages.length > 0 && <p className="ocr-quality-warning" role="status"><TriangleAlert size={14} /> Низкое значение на страницах {lowConfidencePages.map((page) => page.page).join(', ')} (порог {warningThreshold}). Уверенность Tesseract — эвристика, а не вероятность правильного распознавания.</p>}
      {ocrSettingsOpen && <div className="ocr-settings-panel" data-testid="ocr-settings">
        <div className="ocr-settings-fields">
          <label>Язык распознавания<select value={language} onChange={(event) => setLanguage(event.target.value as OcrLanguage)}>
            <option value="rus">Русский</option><option value="eng">Английский</option><option value="rus+eng">Русский + английский</option>
          </select></label>
          <label>Качество<select value={quality} onChange={(event) => setQuality(event.target.value as OcrQuality)}>
            {(Object.keys(OCR_QUALITY) as OcrQuality[]).map((key) => <option value={key} key={key}>{OCR_QUALITY[key].label}</option>)}
          </select><small>{OCR_QUALITY[quality].description}</small></label>
        </div>
        <fieldset className="ocr-page-scope">
          <legend>Какие страницы повторно распознать</legend>
          <label><input type="radio" name={`ocr-scope-${record.id}`} value="all" checked={pageScope === 'all'} onChange={() => setPageScope('all')} /> Все страницы, для которых нужен OCR ({eligiblePages.length})</label>
          <label><input type="radio" name={`ocr-scope-${record.id}`} value="selected" checked={pageScope === 'selected'} onChange={() => setPageScope('selected')} /> Только выбранные страницы</label>
        </fieldset>
        {pageScope === 'all' && eligiblePages.length === 0 && <p className="ocr-settings-limit">Страниц, которым требуется OCR, не найдено. Если нужно распознать текстовый слой заново, выберите страницы вручную.</p>}
        {pageScope === 'selected' && <div className="ocr-pages-select" role="group" aria-label="Страницы для повторного OCR">
          {pageRows.map((page) => <label className="ocr-page-option" key={page.page}>
            <input type="checkbox" checked={selectedPages.has(page.page)} disabled={page.classification === 'blank' || page.ocrResult === 'blank'} onChange={(event) => setSelectedPages((current) => {
              const next = new Set(current)
              if (event.target.checked) {
                if (next.size < maxOcrPages) next.add(page.page)
              } else next.delete(page.page)
              return next
            })} />
            <span><strong>Страница {page.page}</strong><small>{pageStatusLabel(page)}</small></span>
            {page.confidence !== null && page.ocrResult === 'ocr' && <span className="ocr-page-confidence">{Math.round(page.confidence)}</span>}
          </label>)}
        </div>}
        <div className="ocr-page-status-list" data-testid="ocr-page-status-list">
          <details><summary>Статус OCR по страницам</summary><ul>{pageRows.map((page) => <li key={page.page}>
            <span>Страница {page.page}</span><span>{pageStatusLabel(page)}{page.language ? ` · ${page.language}` : ''}{page.dpi ? ` · ${page.dpi} DPI` : ''}</span>
          </li>)}</ul></details>
        </div>
        <p className="ocr-settings-limit">Не более {maxOcrPages} страниц за одно задание. При большем числе сканов запустите OCR для выбранных страниц по частям. Высокое разрешение и два языка требуют больше времени.</p>
        <button type="button" className="button button-dark ocr-start-button" onClick={() => setConfirmReprocess(true)} disabled={reprocessing || (pageScope === 'all' && (eligiblePages.length === 0 || eligiblePages.length > maxOcrPages)) || (pageScope === 'selected' && (selectedPages.size === 0 || selectedPages.size > maxOcrPages))}>
          {reprocessing ? <><LoaderCircle size={14} className="spin" /> OCR выполняется</> : 'Повторить OCR'}
        </button>
        {pageScope === 'all' && eligiblePages.length > maxOcrPages && <small className="ocr-settings-error">В документе больше {maxOcrPages} страниц для OCR. Выберите не более {maxOcrPages} страниц для этого задания.</small>}
      </div>}
      <dialog ref={dialogRef} className="ocr-impact-dialog" aria-labelledby={`ocr-impact-title-${record.id}`} onCancel={(event) => { event.preventDefault(); setConfirmReprocess(false) }} onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect()
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) setConfirmReprocess(false)
      }}>
        <button type="button" className="icon-button ocr-impact-close" aria-label="Закрыть" onClick={() => setConfirmReprocess(false)}><X size={17} /></button>
        <span className="ocr-impact-icon"><Settings2 size={18} /></span>
        <h3 id={`ocr-impact-title-${record.id}`}>Создать новую версию OCR?</h3>
        <p>Будут заново извлечены текст, индекс и ответы{pageScope === 'selected' ? ` для ${selectedPages.size} выбранных страниц` : ' для страниц, которым нужен OCR'}.</p>
        <p className="ocr-impact-preservation">Оригинал документа и история чата сохранятся. Новая версия станет активной только после успешной обработки; при ошибке останется текущая.</p>
        <p className="ocr-impact-choice">{language === 'rus' ? 'Русский' : language === 'eng' ? 'Английский' : 'Русский + английский'} · {OCR_QUALITY[quality].dpi} DPI · {pageScope === 'selected' ? `${selectedPages.size} стр.` : `${eligiblePages.length} стр.`}</p>
        <div className="ocr-impact-actions"><button type="button" className="button button-light" onClick={() => setConfirmReprocess(false)}>Отмена</button><button type="button" className="button button-dark" onClick={() => void submitReprocess()} disabled={reprocessing}>{reprocessing ? 'Обрабатываю…' : 'Создать версию'}</button></div>
      </dialog>
    </section>}
    <SourceCallout source={selectedSource} quality={matchQuality} activeVersion={record.active_version} onReprocess={selectedSource?.locator.ocr === true ? openSourcePageReprocess : undefined} reprocessing={reprocessing} />
    {renderError ? <RenderError preview={preview} originalUrl={originalUrl} message={renderError} onRetry={() => { setRenderError(null); setRetryKey((value) => value + 1) }} /> : <div className="original-render-surface">{content || <RenderError preview={preview} originalUrl={originalUrl} onRetry={() => setRetryKey((value) => value + 1)} />}</div>}
  </div>
}
