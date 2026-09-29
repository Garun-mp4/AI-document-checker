import { useEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, LoaderCircle, TriangleAlert } from 'lucide-react'
import { renderAsync } from 'docx-preview'
import * as pdfjsLib from 'pdfjs-dist'
import type { DocumentPreview, DocumentRecord, PreviewBlock, SourceRef, StreamCitation, TablePreview } from '../types'

const API = '/api/v1'
const CSV_PAGE_SIZE = 100

// Vite copies the worker as a separate asset. Keeping it out of the main
// bundle avoids a blank PDF viewer when the browser blocks an inline worker.
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

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

function RenderError({ preview, originalUrl, onRetry }: { preview: DocumentPreview; originalUrl: string; onRetry: () => void }) {
  return (
    <div className="preview-render-error" role="alert">
      <TriangleAlert size={18} />
      <div><strong>Не удалось отобразить оригинал</strong><span>Документ готов, но браузер не смог построить просмотр.</span></div>
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
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
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

function PdfOriginalViewer({ originalUrl, pageNumber, selectedSource, onMatch, onError }: { originalUrl: string; pageNumber: number; selectedSource: ViewerSource | null; onMatch: (value: boolean) => void; onError: (message: string) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const sheetRef = useRef<HTMLDivElement>(null)
  const [pdf, setPdf] = useState<pdfjsLib.PDFDocumentProxy | null>(null)
  const [items, setItems] = useState<PdfTextItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [rendering, setRendering] = useState(true)
  useEffect(() => {
    let active = true
    setPdf(null); setError(null); setRendering(true)
    void pdfjsLib.getDocument(originalUrl).promise.then((loaded) => { if (active) setPdf(loaded) }).catch((reason: unknown) => { if (active) { const message = reason instanceof Error ? reason.message : 'PDF повреждён'; setError(message); onError(message); setRendering(false) } })
    return () => { active = false }
  }, [originalUrl])
  useEffect(() => {
    if (!pdf || !canvasRef.current || !sheetRef.current) return
    let active = true
    setRendering(true)
    void pdf.getPage(Math.max(1, Math.min(pageNumber, pdf.numPages))).then(async (page) => {
      const baseViewport = page.getViewport({ scale: 1 })
      const scale = Math.min(1.55, 720 / baseViewport.width)
      const viewport = page.getViewport({ scale })
      const canvas = canvasRef.current
      if (!canvas || !active) return
      const ratio = window.devicePixelRatio || 1
      canvas.width = Math.ceil(viewport.width * ratio); canvas.height = Math.ceil(viewport.height * ratio)
      canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`
      sheetRef.current!.style.width = `${viewport.width}px`; sheetRef.current!.style.height = `${viewport.height}px`
      const context = canvas.getContext('2d')
      if (!context) throw new Error('Canvas недоступен')
      await page.render({ canvasContext: context, viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined }).promise
      const content = await page.getTextContent()
      const mapped = content.items.flatMap((item) => {
        if (!('str' in item) || !item.str) return []
        const tx = pdfjsLib.Util.transform(viewport.transform, item.transform)
        const height = Math.max(5, Math.hypot(tx[2], tx[3]))
        return [{ str: item.str, left: tx[4], top: tx[5] - height, width: Math.max(1, item.width * viewport.scale), height }]
      })
      if (active) { setItems(mapped); setRendering(false) }
    }).catch((reason: unknown) => { if (active) { const message = reason instanceof Error ? reason.message : 'Не удалось отобразить страницу'; setError(message); onError(message); setRendering(false) } })
    return () => { active = false }
  }, [pdf, pageNumber])
  useEffect(() => {
    if (!items.length || !selectedSource) return
    const query = sourceQuery(selectedSource)
    const exact = items.some((item) => normalizedIncludes(item.str, query) || query.split(/\s+/).some((word) => word.length > 5 && normalizedIncludes(item.str, word)))
    onMatch(exact)
    if (exact) sheetRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [items, selectedSource, onMatch])
  if (error) return <div className="preview-inline-error">{error}</div>
  const query = sourceQuery(selectedSource)
  return <div className="pdf-original-page-wrap">
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
    setLoading(true); setError(null)
    const container = containerRef.current
    if (!container) return
    container.replaceChildren()
    void fetch(originalUrl).then(async (response) => {
      if (!response.ok) throw new Error('DOCX недоступен')
      const blob = await response.blob()
      await renderAsync(blob, container, undefined, { className: 'docx-preview', breakPages: true, inWrapper: true })
      if (active) setLoading(false)
    }).catch((reason: unknown) => { if (active) { const message = reason instanceof Error ? reason.message : 'DOCX повреждён'; setError(message); onError(message); setLoading(false) } })
    return () => { active = false }
  }, [originalUrl])
  useEffect(() => {
    if (!containerRef.current || !selectedSource || loading) return
    const query = sourceQuery(selectedSource)
    const elements = Array.from(containerRef.current.querySelectorAll<HTMLElement>('p, h1, h2, h3, h4, h5, h6, td, th, li'))
    elements.forEach((element) => element.classList.remove('source-match'))
    const target = elements.find((element) => normalizedIncludes(element.textContent || '', query)) || elements.find((element) => query.split(/\s+/).some((word) => word.length > 5 && normalizedIncludes(element.textContent || '', word)))
    onMatch(Boolean(target))
    target?.classList.add('source-match')
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
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
  const load = async (offset: number, append: boolean) => {
    setLoading(true); setError(null)
    try {
      const response = await fetch(`${API}/documents/${preview.document_id}/preview/table?offset=${offset}&limit=${CSV_PAGE_SIZE}`)
      if (!response.ok) throw new Error('Таблица недоступна')
      const next = await response.json() as TablePreview
      setTable((current) => append && current ? { ...next, rows: [...current.rows, ...next.rows], offset: current.offset, limit: next.limit } : next)
    } catch (reason) { const message = reason instanceof Error ? reason.message : 'Не удалось загрузить таблицу'; setError(message); onError(message) } finally { setLoading(false) }
  }
  useEffect(() => { void load(0, false) }, [preview.document_id])
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
    target && document.getElementById(`csv-row-${target.number}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
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

export function OriginalDocumentViewer({ document: record, preview, selectedSource, selectedSourceId, originalUrl, pageNumber }: OriginalDocumentViewerProps) {
  const [renderError, setRenderError] = useState<string | null>(null)
  const [exactMatch, setExactMatch] = useState(true)
  const [retryKey, setRetryKey] = useState(0)
  const handleMatch = (value: boolean) => setExactMatch(value)
  const renderer = preview.renderer || (record.file_type === 'docx' ? 'docx' : record.file_type === 'pdf' ? 'pdf' : record.file_type === 'csv' ? 'csv' : record.file_type === 'xml' ? 'xml' : 'text')
  const content = useMemo(() => {
    const onError = (message: string) => setRenderError(message)
    if (renderer === 'pdf') return <PdfOriginalViewer key={retryKey} originalUrl={originalUrl} pageNumber={pageNumber} selectedSource={selectedSource} onMatch={handleMatch} onError={onError} />
    if (renderer === 'docx') return <DocxOriginalViewer key={retryKey} originalUrl={originalUrl} selectedSource={selectedSource} onMatch={handleMatch} onError={onError} />
    if (renderer === 'csv') return <CsvOriginalViewer key={retryKey} preview={preview} selectedSource={selectedSource} onMatch={handleMatch} onError={onError} />
    return <TextOriginalViewer key={retryKey} preview={preview} originalUrl={originalUrl} selectedSource={selectedSource} onMatch={handleMatch} onError={onError} />
  }, [originalUrl, pageNumber, preview, record.file_type, renderer, retryKey, selectedSource])
  useEffect(() => { setRenderError(null); setExactMatch(true) }, [preview.document_id, renderer, retryKey])
  return <div className={`original-viewer-body renderer-${renderer}`} data-renderer={renderer} data-selected-source={selectedSourceId || undefined}>
    <div className="preview-toolbar"><span>Оригинал файла{preview.encoding ? ` · ${preview.encoding}` : ''}</span><a href={originalUrl} target="_blank" rel="noreferrer">Открыть исходный файл</a></div>
    <SourceCallout source={selectedSource} exact={exactMatch} />
    {renderError ? <RenderError preview={preview} originalUrl={originalUrl} onRetry={() => { setRenderError(null); setRetryKey((value) => value + 1) }} /> : <div className="original-render-surface">{content || <RenderError preview={preview} originalUrl={originalUrl} onRetry={() => setRetryKey((value) => value + 1)} />}</div>}
  </div>
}
