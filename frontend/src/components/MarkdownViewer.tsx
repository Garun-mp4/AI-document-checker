import { useEffect } from 'react'
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
  const lines = data.markdown.split('\n')
  const range = selectedLines(selectedSource)
  const hasMarkdown = data.status === 'ready' && data.markdown.trim().length > 0
  const loadedChars = Array.from(data.markdown).length
  const hasMore = loadedChars < data.total_chars
  useEffect(() => {
    if (!searchMatch) return
    const frame = window.requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>('.markdown-search-highlight')
      target?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center', inline: 'nearest' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [data.markdown, data.offset, searchMatch?.id])

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
    <div className="markdown-viewer" aria-label="Markdown-представление документа">
      <div className="markdown-viewer-meta">
        <span>{data.total_lines.toLocaleString('ru-RU')} строк · {data.total_chars.toLocaleString('ru-RU')} символов</span>
        {data.converter_version && <span>MarkItDown {data.converter_version}</span>}
      </div>
      <pre className="markdown-source">
        {(() => {
          let codePointCursor = 0
          return lines.map((line, index) => {
            const number = data.line_offset + index
            const active = range.start !== null && number >= range.start && number <= (range.end ?? range.start)
            const chars = Array.from(line)
            const globalStart = data.offset + codePointCursor
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
            codePointCursor += chars.length + (index < lines.length - 1 ? 1 : 0)
            return <span className={`markdown-line ${active ? 'markdown-line-active' : ''}`} data-line={number} key={number}><span className="markdown-line-number">{number}</span><span className="markdown-line-text">{highlighted}</span>{index < lines.length - 1 && '\n'}</span>
          })
        })()}
      </pre>
      {hasMore && <div className="markdown-more"><p className="markdown-truncated">Показана загруженная часть Markdown. Остальное можно подгрузить ниже или скачать целиком.</p>{onLoadMore && <button type="button" className="button button-light" onClick={onLoadMore} disabled={loadingMore}>{loadingMore ? 'Загружаю…' : 'Загрузить ещё'}</button>}</div>}
    </div>
  )
}
