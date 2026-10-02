import { useEffect, useMemo, useRef, useState } from 'react'
import type { DocumentSearchMatch, MarkdownDocument, SourceRef } from '../types'

interface MarkdownViewerProps {
  data: MarkdownDocument
  selectedSource: SourceRef | null
  onRebuild?: () => void
  rebuilding?: boolean
  onLoadMore?: () => void
  loadingMore?: boolean
  searchMatch?: DocumentSearchMatch | null
}

const VIRTUALIZE_AFTER_LINES = 500
const LINE_HEIGHT = 20
const OVERSCAN_LINES = 18

function selectedLines(source: SourceRef | null): { start: number | null; end: number | null } {
  if (!source) return { start: null, end: null }
  const start = source.locator.markdown_line_start
  const end = source.locator.markdown_line_end
  return {
    start: typeof start === 'number' ? start : null,
    end: typeof end === 'number' ? end : null,
  }
}

export function MarkdownViewer({ data, selectedSource, onRebuild, rebuilding = false, onLoadMore, loadingMore = false, searchMatch = null }: MarkdownViewerProps) {
  const viewerRef = useRef<HTMLDivElement>(null)
  const sourceRef = useRef<HTMLPreElement>(null)
  const metaRef = useRef<HTMLDivElement>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(460)
  const [contentStart, setContentStart] = useState(55)
  const lines = useMemo(() => data.markdown.split('\n'), [data.markdown])
  const lineCharOffsets = useMemo(() => {
    const offsets = new Array<number>(lines.length)
    let cursor = 0
    for (let index = 0; index < lines.length; index += 1) {
      offsets[index] = cursor
      cursor += Array.from(lines[index] ?? '').length + (index < lines.length - 1 ? 1 : 0)
    }
    return offsets
  }, [lines])
  const range = selectedLines(selectedSource)
  const hasMarkdown = data.status === 'ready' && data.markdown.trim().length > 0
  const loadedChars = Array.from(data.markdown).length
  const hasMore = loadedChars < data.total_chars
  const virtualized = lines.length > VIRTUALIZE_AFTER_LINES
  const rangeStart = virtualized
    ? Math.max(0, Math.floor(Math.max(0, scrollTop - contentStart) / LINE_HEIGHT) - OVERSCAN_LINES)
    : 0
  const rangeEnd = virtualized
    ? Math.min(lines.length, Math.ceil((scrollTop + viewportHeight - contentStart) / LINE_HEIGHT) + OVERSCAN_LINES)
    : lines.length
  const visibleLines = lines.slice(rangeStart, rangeEnd)

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return
    const meta = metaRef.current
    const source = sourceRef.current
    const updateMetrics = () => {
      setViewportHeight(viewer.clientHeight || 460)
      if (source) {
        const start = source.getBoundingClientRect().top - viewer.getBoundingClientRect().top + viewer.scrollTop + 13
        setContentStart(start)
      }
    }
    updateMetrics()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(updateMetrics)
    observer.observe(viewer)
    if (meta) observer.observe(meta)
    return () => observer.disconnect()
  }, [])

  const targetLine = useMemo(() => {
    if (searchMatch?.markdown_start !== null && searchMatch?.markdown_start !== undefined) {
      const localIndex = searchMatch.markdown_start - data.offset
      if (localIndex >= 0 && localIndex <= data.markdown.length) {
        return data.line_offset + data.markdown.slice(0, localIndex).split('\n').length - 1
      }
    }
    return range.start
  }, [data.line_offset, data.markdown, data.offset, range.start, searchMatch?.markdown_start])

  useEffect(() => {
    const viewer = viewerRef.current
    const source = sourceRef.current
    if (!viewer || !source || targetLine === null || targetLine === undefined) return
    const localLine = targetLine - data.line_offset
    if (localLine < 0 || localLine >= lines.length) return
    const frame = window.requestAnimationFrame(() => {
      const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      const sourceTop = source.getBoundingClientRect().top - viewer.getBoundingClientRect().top + viewer.scrollTop
      const targetTop = sourceTop + 13 + localLine * LINE_HEIGHT
      viewer.scrollTo({ top: Math.max(0, targetTop - 36), behavior: reducedMotion ? 'auto' : 'smooth' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [data.line_offset, data.markdown, data.offset, lines.length, targetLine])

  if (!hasMarkdown) {
    return (
      <div className="markdown-empty" role={data.error ? 'alert' : 'status'}>
        <strong>{data.status === 'fallback' ? 'Анализ построен резервным способом' : data.status === 'legacy' ? 'Markdown ещё не создан' : 'Markdown недоступен'}</strong>
        <p>{data.error || (data.status === 'legacy' ? 'Для старого документа запустите создание Markdown повторно.' : 'После обработки здесь появится представление документа.')}</p>
        {onRebuild && (data.status === 'legacy' || data.status === 'fallback' || data.status === 'failed') && <button type="button" className="button button-light" onClick={onRebuild} disabled={rebuilding}>{rebuilding ? 'Создаю…' : 'Создать Markdown'}</button>}
      </div>
    )
  }

  return (
    <div
      className={`markdown-viewer${virtualized ? ' is-virtualized' : ''}`}
      aria-label="Markdown-представление документа"
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
      ref={viewerRef}
    >
      <div className="markdown-viewer-meta" ref={metaRef}>
        <span>{data.total_lines.toLocaleString('ru-RU')} строк · {data.total_chars.toLocaleString('ru-RU')} символов</span>
        {data.converter_version && <span>MarkItDown {data.converter_version}</span>}
        {virtualized && <span>Прокрутка без загрузки всех строк в разметку</span>}
      </div>
      <pre className="markdown-source" ref={sourceRef}>
        {virtualized && <span className="markdown-virtual-spacer" aria-hidden="true" style={{ height: rangeStart * LINE_HEIGHT }} />}
        {visibleLines.map((line, visibleIndex) => {
          const index = rangeStart + visibleIndex
          const number = data.line_offset + index
          const active = range.start !== null && number >= range.start && number <= (range.end ?? range.start)
          const chars = Array.from(line)
          const globalStart = data.offset + (lineCharOffsets[index] ?? 0)
          const hitStart = searchMatch?.markdown_start
          const hitEnd = searchMatch?.markdown_end
          const from = hitStart === null || hitStart === undefined || hitEnd === null || hitEnd === undefined
            ? -1
            : Math.max(0, hitStart - globalStart)
          const to = hitStart === null || hitStart === undefined || hitEnd === null || hitEnd === undefined
            ? -1
            : Math.min(chars.length, hitEnd - globalStart)
          const highlighted = from >= 0 && to > from
            ? <>{chars.slice(0, from).join('')}<mark className="markdown-search-highlight" data-search-match="true">{chars.slice(from, to).join('')}</mark>{chars.slice(to).join('')}</>
            : line || ' '
          return <span className={`markdown-line ${active ? 'markdown-line-active' : ''}`} data-line={number} key={number}><span className="markdown-line-number">{number}</span><span className="markdown-line-text">{highlighted}</span></span>
        })}
        {virtualized && <span className="markdown-virtual-spacer" aria-hidden="true" style={{ height: Math.max(0, lines.length - rangeEnd) * LINE_HEIGHT }} />}
      </pre>
      {hasMore && <div className="markdown-more"><p className="markdown-truncated">Показана загруженная часть Markdown. Остальное можно подгрузить ниже или скачать целиком.</p>{onLoadMore && <button type="button" className="button button-light" onClick={onLoadMore} disabled={loadingMore}>{loadingMore ? 'Загружаю…' : 'Загрузить ещё'}</button>}</div>}
    </div>
  )
}
