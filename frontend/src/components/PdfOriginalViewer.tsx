import { useEffect, useRef, useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import * as pdfjsLib from 'pdfjs-dist'
import type { MatchQuality, ViewerSource } from './documentViewerUtils'
import { clearTextHighlight, installTextHighlight, isCalculation, navigateToSource, normalizedRange, pdfOcrWordBoxes, sourceQuery, supportsTextHighlights } from './documentViewerUtils'

// This dynamically imported module references the worker asset, so neither is
// fetched before a PDF is opened. A retry gets a distinct URL so a rejected
// worker fetch cannot poison the next attempt through the browser module map.
const pdfWorkerAssetUrl = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url)

interface PdfTextItem { str: string; left: number; top: number; width: number; height: number }

function isPdfCancellation(reason: unknown): boolean {
  return reason instanceof Error && (reason.name === 'RenderingCancelledException' || reason.name === 'AbortException')
}

export interface PdfOriginalViewerProps {
  originalUrl: string
  pageNumber: number
  selectedSource: ViewerSource | null
  onMatch: (value: MatchQuality) => void
  onRetry: () => void
  retryAttempt: number
}

export function PdfOriginalViewer({ originalUrl, pageNumber, selectedSource, onMatch, onRetry, retryAttempt }: PdfOriginalViewerProps) {
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
    const workerUrl = new URL(pdfWorkerAssetUrl)
    workerUrl.searchParams.set('v', 'pdfjs-4')
    if (retryAttempt > 0) workerUrl.searchParams.set('retry', String(retryAttempt))
    pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl.toString()
    const loadingTask = pdfjsLib.getDocument({ url: originalUrl })
    void loadingTask.promise.then((loaded) => { if (active) setPdf(loaded) }).catch((reason: unknown) => {
      if (!active || isPdfCancellation(reason)) return
      const message = reason instanceof Error ? reason.message : 'PDF повреждён'
      setError(message); setNativeFallback(true); setRendering(false)
    })
    return () => { active = false; void loadingTask.destroy().catch(() => undefined) }
  }, [originalUrl, retryAttempt])

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

        // The canvas is authoritative: bad text metadata must not hide a page already rendered.
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
    clearTextHighlight('document-source-range')
    if (!selectedSource) return
    if (isCalculation(selectedSource)) { onMatch('calculation'); return }
    const sourcePage = typeof selectedSource?.locator.page === 'number' ? selectedSource.locator.page : null
    if (sourcePage !== null && sourcePage !== pageNumber) { onMatch('page_only'); return }
    if (selectedSource?.locator.ocr === true && sourcePage === pageNumber) {
      const boxes = pdfOcrWordBoxes(selectedSource)
      onMatch(boxes.length > 0 ? 'exact' : sourcePage !== null ? 'page_only' : 'not_found')
      const target = boxes.length ? sheetRef.current?.querySelector('.pdf-ocr-highlight-box') : sheetRef.current
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
    return () => clearTextHighlight('document-source-range')
  }, [items, pageNumber, selectedSource, onMatch])

  if (nativeFallback) return <div className="pdf-native-fallback">
    <div className="pdf-native-fallback-note">
      <strong>Встроенный просмотр PDF.js недоступен</strong>
      <span role="status" aria-live="polite">Показываю оригинал через просмотрщик браузера{error ? ` · ${error}` : ''}.</span>
      <button type="button" className="button button-light" onClick={onRetry}>Повторить встроенный просмотр</button>
    </div>
    <iframe title="Оригинальный PDF-документ" src={`${originalUrl}#page=${Math.max(1, pageNumber)}`} />
  </div>
  if (error) return <div className="preview-inline-error" role="alert">{error}<button type="button" className="button button-light" onClick={onRetry}>Повторить</button></div>
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

  return <div className="pdf-original-page-wrap" ref={viewerRef}>
    {rendering && <div className="viewer-loading" role="status" aria-live="polite"><LoaderCircle className="spin" size={18} /> Рендерю страницу {pageNumber}…</div>}
    <div className="pdf-page-sheet" ref={sheetRef}>
      <canvas ref={canvasRef} />
      {selectedSource?.locator.ocr === true && selectedSource.locator.page === pageNumber && <div className="pdf-ocr-highlight-layer" aria-hidden="true">
        {pdfOcrWordBoxes(selectedSource).map((box) => <span className="pdf-ocr-highlight-box" key={box.key} style={{ left: `${box.left}%`, top: `${box.top}%`, width: `${box.width}%`, height: `${box.height}%` }} />)}
      </div>}
      <div className="pdf-text-layer" aria-hidden="true">
        {items.map((item, index) => {
          const match = matchIndexes.has(index)
          return <span ref={(node) => { if (node) textSpansRef.current.set(index, node); else textSpansRef.current.delete(index) }} key={`${item.left}-${item.top}-${index}`} className={`${match ? 'pdf-text-match' : ''}${match && !supportsTextHighlights() ? ' pdf-text-highlight-fallback' : ''}`} style={{ left: item.left, top: item.top, width: item.width, height: item.height, fontSize: item.height }}>{item.str}</span>
        })}
      </div>
    </div>
  </div>
}
