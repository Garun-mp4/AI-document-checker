import { useEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, LoaderCircle, TriangleAlert } from 'lucide-react'
import { renderAsync } from 'docx-preview'
import * as pdfjsLib from 'pdfjs-dist'
import type { DocumentPreview, DocumentRecord, PreviewBlock, SourceRef, StreamCitation, TablePreview } from '../types'

const API = '/api/v1'
const CSV_PAGE_SIZE = 100

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
}

function sourceQuery(source: ViewerSource | null): string {
  if (!source) return ''
  const value = source.text.replace(/\s+/g, ' ').trim()
  if (!value) return ''
  return value.length > 100 ? value.slice(0, 100).replace(/\s+\S*$/, '') : value
}

function normalizedIncludes(value: string, query: string): boolean {
  if (!query) return false
  return value.replace(/\s+/g, ' ').toLocaleLowerCase().includes(query.replace(/\s+/g, ' ').toLocaleLowerCase())
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

function SourceCallout({ source, exact }: { source: ViewerSource | null; exact: boolean }) {
  if (!source) return null
  return (
    <div className="preview-source-callout" role="status">
      <div className="preview-source-callout-heading"><BookOpen size={14} /><span>Выбран источник · {locatorLabel(source)}</span></div>
      <p>{source.text}</p>
      {!exact && <small>Точное место не найдено, показана ближайшая область.</small>}
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
    const query = sourceQuery(selectedSource)
    const lineStart = typeof selectedSource.locator.line_start === 'number' ? selectedSource.locator.line_start : null
    const lineEnd = typeof selectedSource.locator.line_end === 'number' ? selectedSource.locator.line_end : lineStart
    const nodes = Array.from(linesRef.current.querySelectorAll<HTMLElement>('[data-line]'))
    let exact = false
    let target: HTMLElement | undefined
    for (const node of nodes) {
      const line = node.textContent || ''
      const number = Number(node.dataset.line)
      const inRange = lineStart !== null && number >= lineStart && number <= (lineEnd ?? lineStart)
      if (normalizedIncludes(line, query)) { exact = true; target ??= node }
      if (!target && inRange) target = node
    }
    onMatch(exact)
    scrollIntoViewRespectingMotion(target)
  }, [text, selectedSource, onMatch])

  if (error) return <div className="preview-inline-error">{error}</div>
  if (text === null) return <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Загружаю оригинальный текст…</div>
  const query = sourceQuery(selectedSource)
  const lineStart = typeof selectedSource?.locator.line_start === 'number' ? selectedSource.locator.line_start : null
  const lineEnd = typeof selectedSource?.locator.line_end === 'number' ? selectedSource.locator.line_end : lineStart
  const lines = text.split(/\r?\n/)
  return (
    <div className="original-text-viewer" ref={linesRef}>
      {lines.map((line, index) => {
        const number = index + 1
        const inRange = lineStart !== null && number >= lineStart && number <= (lineEnd ?? lineStart)
        const match = query && normalizedIncludes(line, query)
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
    if (!items.length || !selectedSource) return
    const query = sourceQuery(selectedSource)
    const exact = items.some((item) => normalizedIncludes(item.str, query) || query.split(/\s+/).some((word) => word.length > 5 && normalizedIncludes(item.str, word)))
    onMatch(exact)
    if (exact) scrollIntoViewRespectingMotion(sheetRef.current)
  }, [items, selectedSource, onMatch])
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

export function OriginalDocumentViewer({ document: record, preview, selectedSource, selectedSourceId, originalUrl, pageNumber }: OriginalDocumentViewerProps) {
  const [renderError, setRenderError] = useState<string | null>(null)
  const [exactMatch, setExactMatch] = useState(true)
  const [retryKey, setRetryKey] = useState(0)
  const handleMatch = (value: boolean) => setExactMatch(value)
  const renderer = preview.renderer || (record.file_type === 'docx' ? 'docx' : record.file_type === 'pdf' ? 'pdf' : ['csv', 'xlsx', 'xls'].includes(record.file_type) ? record.file_type : record.file_type === 'xml' ? 'xml' : record.file_type === 'pptx' || record.file_type === 'epub' ? record.file_type : 'text')
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
    <SourceCallout source={selectedSource} exact={exactMatch} />
    {renderError ? <RenderError preview={preview} originalUrl={originalUrl} message={renderError} onRetry={() => { setRenderError(null); setRetryKey((value) => value + 1) }} /> : <div className="original-render-surface">{content || <RenderError preview={preview} originalUrl={originalUrl} onRetry={() => setRetryKey((value) => value + 1)} />}</div>}
  </div>
}
