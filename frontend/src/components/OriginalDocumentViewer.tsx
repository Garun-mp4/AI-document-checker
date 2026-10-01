import { useEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, ChevronDown, LoaderCircle, Settings2, TriangleAlert, X } from 'lucide-react'
import { renderAsync } from 'docx-preview'
import * as pdfjsLib from 'pdfjs-dist'
import type { DocumentPreview, DocumentRecord, PreviewBlock, SourceRef, StreamCitation, TablePreview } from '../types'

const API = '/api/v1'
const CSV_PAGE_SIZE = 100

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
  const value = source.text.replace(/\s+/g, ' ').trim()
  if (!value) return ''
  return value.length > 100 ? value.slice(0, 100).replace(/\s+\S*$/, '') : value
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

function SourceCallout({ source, exact, onReprocess, reprocessing = false }: { source: ViewerSource | null; exact: boolean; onReprocess?: () => void; reprocessing?: boolean }) {
  if (!source) return null
  const needsOcrRecovery = source.locator.ocr === true && pdfOcrWordBoxes(source).length === 0
  return (
    <div className="preview-source-callout" role="status">
      <div className="preview-source-callout-heading"><BookOpen size={14} /><span>Выбран источник · {locatorLabel(source)}</span></div>
      <p>{source.text}</p>
      {needsOcrRecovery
        ? <><small>У этого старого результата OCR сохранена страница, но нет координат точной подсветки. Повторная обработка создаст карту координат.</small>
          {onReprocess && <button className="button button-light preview-reprocess-button" type="button" onClick={onReprocess} disabled={reprocessing}>{reprocessing ? <><LoaderCircle size={14} className="spin" /> Создаю карту координат…</> : 'Создать карту координат'}</button>}</>
        : !exact && <small>Точное место не найдено, показана ближайшая область.</small>}
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

function TextOriginalViewer({ preview, originalUrl, selectedSource, onMatch, onError }: { preview: DocumentPreview; originalUrl: string; selectedSource: ViewerSource | null; onMatch: (value: boolean) => void; onError: (message: string) => void }) {
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
    if (!text || !selectedSource || !linesRef.current) return
    const queries = textSourceQueries(preview, selectedSource)
    const lineStart = typeof selectedSource.locator.line_start === 'number' ? selectedSource.locator.line_start : null
    const lineEnd = typeof selectedSource.locator.line_end === 'number' ? selectedSource.locator.line_end : lineStart
    const nodes = Array.from(linesRef.current.querySelectorAll<HTMLElement>('[data-line]'))
    let exact = false
    let target: HTMLElement | undefined
    for (const node of nodes) {
      const line = node.textContent || ''
      const number = Number(node.dataset.line)
      const inRange = lineStart !== null && number >= lineStart && number <= (lineEnd ?? lineStart)
      if (queries.some((query) => normalizedIncludes(line, query))) { exact = true; target ??= node }
      if (!target && inRange) target = node
    }
    onMatch(exact)
    scrollIntoViewRespectingMotion(target)
  }, [text, selectedSource, onMatch, preview.renderer])

  if (error) return <div className="preview-inline-error">{error}</div>
  if (text === null) return <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Загружаю оригинальный текст…</div>
  const queries = textSourceQueries(preview, selectedSource)
  const lineStart = typeof selectedSource?.locator.line_start === 'number' ? selectedSource.locator.line_start : null
  const lineEnd = typeof selectedSource?.locator.line_end === 'number' ? selectedSource.locator.line_end : lineStart
  const lines = text.split(/\r?\n/)
  return (
    <div className="original-text-viewer" ref={linesRef}>
      {lines.map((line, index) => {
        const number = index + 1
        const inRange = lineStart !== null && number >= lineStart && number <= (lineEnd ?? lineStart)
        const match = queries.some((query) => normalizedIncludes(line, query))
        return <div className={`source-text-line ${inRange ? 'source-line-range' : ''}`} data-line={number} key={number}>
          <span className="source-line-number">{number}</span>
          <code>{match ? <mark>{line || ' '}</mark> : inRange && selectedSource ? <mark>{line || ' '}</mark> : line || ' '}</code>
        </div>
      })}
    </div>
  )
}

interface PdfTextItem { str: string; left: number; top: number; width: number; height: number }

function isPdfCancellation(reason: unknown): boolean {
  return reason instanceof Error && (reason.name === 'RenderingCancelledException' || reason.name === 'AbortException')
}

function PdfOriginalViewer({ originalUrl, pageNumber, selectedSource, onMatch }: { originalUrl: string; pageNumber: number; selectedSource: ViewerSource | null; onMatch: (value: boolean) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const sheetRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<HTMLDivElement>(null)
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
    const sourcePage = typeof selectedSource?.locator.page === 'number' ? selectedSource.locator.page : null
    if (selectedSource?.locator.ocr === true && sourcePage === pageNumber) {
      const boxes = pdfOcrWordBoxes(selectedSource)
      onMatch(boxes.length > 0)
      const target = boxes.length
        ? sheetRef.current?.querySelector('.pdf-ocr-highlight-box')
        : sheetRef.current
      scrollIntoViewRespectingMotion(target)
      return
    }
    if (!items.length || !selectedSource) return
    const query = sourceQuery(selectedSource)
    const exact = items.some((item) => normalizedIncludes(item.str, query) || query.split(/\s+/).some((word) => word.length > 5 && normalizedIncludes(item.str, word)))
    onMatch(exact)
    if (exact) scrollIntoViewRespectingMotion(sheetRef.current)
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
  return <div className="pdf-original-page-wrap" ref={viewerRef}>
    {rendering && <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Рендерю страницу {pageNumber}…</div>}
    <div className="pdf-page-sheet" ref={sheetRef}>
      <canvas ref={canvasRef} />
      {selectedSource?.locator.ocr === true && selectedSource.locator.page === pageNumber && <div className="pdf-ocr-highlight-layer" aria-hidden="true">
        {pdfOcrWordBoxes(selectedSource).map((box) => <span className="pdf-ocr-highlight-box" key={box.key} style={{ left: `${box.left}%`, top: `${box.top}%`, width: `${box.width}%`, height: `${box.height}%` }} />)}
      </div>}
      <div className="pdf-text-layer" aria-hidden="true">
        {items.map((item, index) => {
          const match = Boolean(query && (normalizedIncludes(item.str, query) || query.split(/\s+/).some((word) => word.length > 5 && normalizedIncludes(item.str, word))))
          return <span key={`${item.left}-${item.top}-${index}`} className={match ? 'pdf-text-match' : ''} style={{ left: item.left, top: item.top, width: item.width, height: item.height, fontSize: item.height }}>{item.str}</span>
        })}
      </div>
    </div>
  </div>
}

function DocxOriginalViewer({ originalUrl, selectedSource, onMatch, onError }: { originalUrl: string; selectedSource: ViewerSource | null; onMatch: (value: boolean) => void; onError: (message: string) => void }) {
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
    if (!containerRef.current || !selectedSource || loading) return
    const query = sourceQuery(selectedSource)
    const elements = Array.from(containerRef.current.querySelectorAll<HTMLElement>('p, h1, h2, h3, h4, h5, h6, td, th, li'))
    elements.forEach((element) => element.classList.remove('source-match'))
    const target = elements.find((element) => normalizedIncludes(element.textContent || '', query)) || elements.find((element) => query.split(/\s+/).some((word) => word.length > 5 && normalizedIncludes(element.textContent || '', word)))
    onMatch(Boolean(target))
    target?.classList.add('source-match')
    scrollIntoViewRespectingMotion(target)
  }, [loading, selectedSource, onMatch])
  if (error) return <div className="preview-inline-error">{error}</div>
  return <div className="docx-original-viewer">
    <div className="docx-render-host" ref={containerRef} />
    {loading && <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Рендерю оригинал DOCX…</div>}
  </div>
}

function CsvOriginalViewer({ preview, selectedSource, onMatch, onError }: { preview: DocumentPreview; selectedSource: ViewerSource | null; onMatch: (value: boolean) => void; onError: (message: string) => void }) {
  const [table, setTable] = useState<TablePreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const tableRef = useRef<HTMLDivElement>(null)
  const requestRef = useRef(0)
  const load = async (offset: number, append: boolean) => {
    const requestId = ++requestRef.current
    const controller = new AbortController()
    setLoading(true); setError(null)
    try {
      const response = await fetch(`${API}/documents/${preview.document_id}/preview/table?offset=${offset}&limit=${CSV_PAGE_SIZE}`, { signal: controller.signal })
      if (!response.ok) throw new Error('Таблица недоступна')
      const next = await response.json() as TablePreview
      if (requestId !== requestRef.current) return
      setTable((current) => append && current ? { ...next, rows: [...current.rows, ...next.rows], offset: current.offset, limit: next.limit } : next)
    } catch (reason) {
      if (requestId !== requestRef.current || (reason instanceof DOMException && reason.name === 'AbortError')) return
      const message = reason instanceof Error ? reason.message : 'Не удалось загрузить таблицу'
      setError(message); onError(message)
    } finally {
      if (requestId === requestRef.current) setLoading(false)
    }
  }
  useEffect(() => {
    void load(0, false)
    return () => { requestRef.current += 1 }
  }, [preview.document_id])
  useEffect(() => {
    if (!table || !selectedSource) return
    const start = typeof selectedSource.locator.row_start === 'number' ? selectedSource.locator.row_start : null
    if (start === null) return
    const end = typeof selectedSource.locator.row_end === 'number' ? selectedSource.locator.row_end : start
    const loaded = table.rows.some((row) => row.number >= start && row.number <= end)
    if (!loaded && start > 1 && start - 2 < table.total_rows) { void load(Math.max(0, start - 2), false); return }
    const target = table.rows.find((row) => row.number >= start && row.number <= end)
    const exact = Boolean(target)
    onMatch(exact)
    if (target) scrollIntoViewRespectingMotion(document.getElementById(`csv-row-${target.number}`))
  }, [table, selectedSource, onMatch])
  if (error) return <div className="preview-inline-error">{error}</div>
  if (!table) return <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Загружаю оригинальную таблицу…</div>
  const start = typeof selectedSource?.locator.row_start === 'number' ? selectedSource.locator.row_start : null
  const end = typeof selectedSource?.locator.row_end === 'number' ? selectedSource.locator.row_end : start
  return <div className="csv-original-viewer" ref={tableRef}>
    <div className="csv-table-scroll">
      <table className="original-csv-table"><thead><tr><th scope="col">№</th>{table.columns.map((column) => <th scope="col" key={column}>{column}</th>)}</tr></thead>
        <tbody>{table.rows.map((row) => <tr id={`csv-row-${row.number}`} className={start !== null && row.number >= start && row.number <= (end ?? start) ? 'source-row-match' : ''} key={row.number}><td className="csv-row-number">{row.number}</td>{row.cells.map((cell, index) => <td key={`${row.number}-${index}`}>{cell || '—'}</td>)}</tr>)}</tbody>
      </table>
    </div>
    <div className="csv-table-footer"><span>Показано {table.rows.length} из {table.total_rows} строк</span>{table.rows.length < table.total_rows && <button type="button" className="button button-light" onClick={() => void load(table.rows.length, true)} disabled={loading}>{loading ? 'Загружаю…' : 'Показать ещё'}</button>}</div>
  </div>
}

function SourceMapOriginalViewer({ preview, selectedSource, onMatch }: { preview: DocumentPreview; selectedSource: ViewerSource | null; onMatch: (value: boolean) => void }) {
  const query = sourceQuery(selectedSource)
  const selectedId = selectedSource?.id
  useEffect(() => {
    if (!selectedSource) return
    const target = Array.from(document.querySelectorAll<HTMLElement>('[data-source-id]')).find((element) => element.dataset.sourceId === selectedSource.id)
    const exact = Boolean(preview.blocks.find((block) => block.source_id === selectedId || normalizedIncludes(block.text, query)))
    onMatch(exact)
    scrollIntoViewRespectingMotion(target)
  }, [preview.blocks, query, selectedId, selectedSource, onMatch])
  return <div className="source-map-original-viewer">
    {preview.blocks.map((block) => {
      const active = block.source_id === selectedId || Boolean(query && normalizedIncludes(block.text, query))
      return <article className={`preview-block ${active ? 'source-match' : ''}`} data-source-id={block.source_id} key={block.id}>
        <div className="preview-block-meta"><span>{locatorLabel(block)}</span></div>
        <p>{block.text}</p>
      </article>
    })}
  </div>
}

export function OriginalDocumentViewer({ document: record, preview, selectedSource, selectedSourceId, originalUrl, pageNumber, onReprocess, reprocessing = false }: OriginalDocumentViewerProps) {
  const [renderError, setRenderError] = useState<string | null>(null)
  const [exactMatch, setExactMatch] = useState(true)
  const [retryKey, setRetryKey] = useState(0)
  const [ocrSettingsOpen, setOcrSettingsOpen] = useState(false)
  const [language, setLanguage] = useState<OcrLanguage>('rus+eng')
  const [quality, setQuality] = useState<OcrQuality>('balanced')
  const [pageScope, setPageScope] = useState<'all' | 'selected'>('all')
  const [selectedPages, setSelectedPages] = useState<Set<number>>(new Set())
  const [confirmReprocess, setConfirmReprocess] = useState(false)
  const dialogRef = useRef<HTMLDialogElement>(null)
  const handleMatch = (value: boolean) => setExactMatch(value)
  const renderer = preview.renderer || (record.file_type === 'docx' ? 'docx' : record.file_type === 'pdf' ? 'pdf' : ['csv', 'xlsx', 'xls'].includes(record.file_type) ? record.file_type : record.file_type === 'xml' ? 'xml' : record.file_type === 'pptx' || record.file_type === 'epub' ? record.file_type : 'text')
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
    if (renderer === 'pdf') return <PdfOriginalViewer key={retryKey} originalUrl={originalUrl} pageNumber={pageNumber} selectedSource={selectedSource} onMatch={handleMatch} />
    if (renderer === 'docx') return <DocxOriginalViewer key={retryKey} originalUrl={originalUrl} selectedSource={selectedSource} onMatch={handleMatch} onError={onError} />
    if (renderer === 'csv' || renderer === 'xlsx' || renderer === 'xls') return <CsvOriginalViewer key={retryKey} preview={preview} selectedSource={selectedSource} onMatch={handleMatch} onError={onError} />
    if (renderer === 'pptx' || renderer === 'epub') return <SourceMapOriginalViewer key={retryKey} preview={preview} selectedSource={selectedSource} onMatch={handleMatch} />
    return <TextOriginalViewer key={retryKey} preview={preview} originalUrl={originalUrl} selectedSource={selectedSource} onMatch={handleMatch} onError={onError} />
  }, [originalUrl, pageNumber, preview, record.file_type, renderer, retryKey, selectedSource])
  useEffect(() => { setRenderError(null); setExactMatch(true) }, [preview.document_id, renderer, retryKey])
  return <div className={`original-viewer-body renderer-${renderer}`} data-renderer={renderer} data-selected-source={selectedSourceId || undefined}>
    <div className="preview-toolbar"><span>Оригинал файла{preview.encoding ? ` · ${preview.encoding}` : ''}</span><a href={originalUrl} target="_blank" rel="noreferrer">Открыть исходный файл</a></div>
    {renderer === 'pdf' && <section className="ocr-panel" aria-label="Статус и настройки OCR" data-testid="ocr-panel">
      <div className="ocr-panel-summary">
        <BookOpen size={15} aria-hidden="true" />
        <span>{statusCounts.recognized} OCR · {statusCounts.native} текстовых · {statusCounts.blank} пустых{statusCounts.unreadable ? ` · ${statusCounts.unreadable} требуют внимания` : ''}</span>
        {onReprocess && <button className="ocr-panel-toggle" type="button" aria-expanded={ocrSettingsOpen} onClick={() => setOcrSettingsOpen((open) => !open)}>
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
    <SourceCallout source={selectedSource} exact={exactMatch} onReprocess={selectedSource?.locator.ocr === true ? openSourcePageReprocess : undefined} reprocessing={reprocessing} />
    {renderError ? <RenderError preview={preview} originalUrl={originalUrl} message={renderError} onRetry={() => { setRenderError(null); setRetryKey((value) => value + 1) }} /> : <div className="original-render-surface">{content || <RenderError preview={preview} originalUrl={originalUrl} onRetry={() => setRetryKey((value) => value + 1)} />}</div>}
  </div>
}
