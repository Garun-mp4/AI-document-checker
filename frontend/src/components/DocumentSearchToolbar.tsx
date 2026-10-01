import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowDown, ArrowUp, LoaderCircle, Search, X } from 'lucide-react'
import type { DocumentSearchMatch, DocumentSearchResponse, DocumentSearchScope } from '../types'

const API = '/api/v1'
const PAGE_SIZE = 50

interface DocumentSearchToolbarProps {
  documentId: string
  resetKey: number
  scope: DocumentSearchScope
  onScopeChange: (scope: DocumentSearchScope) => void
  onNavigate: (match: DocumentSearchMatch, scope: DocumentSearchScope) => void | Promise<void>
  onClear: () => void
}

async function requestSearch(documentId: string, scope: DocumentSearchScope, query: string, offset: number, signal: AbortSignal): Promise<DocumentSearchResponse> {
  const params = new URLSearchParams({ q: query, scope, offset: String(offset), limit: String(PAGE_SIZE) })
  const response = await fetch(API + '/documents/' + encodeURIComponent(documentId) + '/search?' + params, { signal })
  if (!response.ok) {
    let message = 'Поиск недоступен (' + response.status + ')'
    try {
      const body = await response.json() as { detail?: string }
      if (body.detail) message = body.detail
    } catch { /* Keep the HTTP status as a fallback. */ }
    throw new Error(message)
  }
  return await response.json() as DocumentSearchResponse
}

export function DocumentSearchToolbar({ documentId, resetKey, scope, onScopeChange, onNavigate, onClear }: DocumentSearchToolbarProps) {
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState<DocumentSearchMatch[]>([])
  const [total, setTotal] = useState(0)
  const [currentIndex, setCurrentIndex] = useState(-1)
  const [pageOffset, setPageOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const generationRef = useRef(0)
  const pageRequestRef = useRef<AbortController | null>(null)
  const matchesRef = useRef(matches)
  matchesRef.current = matches

  const invalidate = () => {
    generationRef.current += 1
    pageRequestRef.current?.abort()
    pageRequestRef.current = null
  }

  useEffect(() => {
    generationRef.current += 1
    pageRequestRef.current?.abort()
    pageRequestRef.current = null
    setQuery('')
    setMatches([])
    setTotal(0)
    setCurrentIndex(-1)
    setPageOffset(0)
    setError('')
    setLoading(false)
    // Reset only this toolbar's transient state. The parent owns source selection
    // and may be resetting the query because a citation was opened.
  }, [documentId, resetKey])

  useEffect(() => {
    const trimmed = query.trim()
    const generation = ++generationRef.current
    const controller = new AbortController()
    pageRequestRef.current?.abort()
    pageRequestRef.current = null
    setMatches([])
    setTotal(0)
    setCurrentIndex(-1)
    setPageOffset(0)
    setError('')
    if (!trimmed) {
      setLoading(false)
      return () => controller.abort()
    }
    setLoading(true)
    const timer = window.setTimeout(() => {
      void requestSearch(documentId, scope, trimmed, 0, controller.signal).then(async (result) => {
        if (generation !== generationRef.current) return
        setMatches(result.matches)
        setTotal(result.total)
        setPageOffset(result.offset)
        if (result.matches.length) {
          setCurrentIndex(0)
          await onNavigate(result.matches[0], scope)
        } else {
          setCurrentIndex(-1)
          onClear()
        }
      }).catch((reason: unknown) => {
        if (generation !== generationRef.current || (reason instanceof DOMException && reason.name === 'AbortError')) return
        setMatches([])
        setTotal(0)
        setCurrentIndex(-1)
        setError(reason instanceof Error ? reason.message : 'Не удалось выполнить поиск.')
        onClear()
      }).finally(() => {
        if (generation === generationRef.current) setLoading(false)
      })
    }, 280)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [documentId, query, scope, onNavigate, onClear])

  const clearQuery = () => {
    invalidate()
    setQuery('')
    setMatches([])
    setTotal(0)
    setCurrentIndex(-1)
    setPageOffset(0)
    setError('')
    setLoading(false)
    onClear()
  }

  const navigate = async (nextIndex: number) => {
    if (nextIndex < 0 || nextIndex >= total) return
    const generation = generationRef.current
    let page = matchesRef.current
    let pageStart = pageOffset
    if (nextIndex < pageStart || nextIndex >= pageStart + page.length) {
      pageRequestRef.current?.abort()
      const controller = new AbortController()
      pageRequestRef.current = controller
      setLoading(true)
      try {
        const result = await requestSearch(documentId, scope, query.trim(), Math.floor(nextIndex / PAGE_SIZE) * PAGE_SIZE, controller.signal)
        if (generation !== generationRef.current) return
        page = result.matches
        pageStart = result.offset
        setMatches(page)
        setPageOffset(pageStart)
      } catch (reason) {
        if (generation !== generationRef.current || (reason instanceof DOMException && reason.name === 'AbortError')) return
        setError(reason instanceof Error ? reason.message : 'Не удалось загрузить результаты поиска.')
        onClear()
        return
      } finally {
        if (generation === generationRef.current) setLoading(false)
      }
    }
    if (generation !== generationRef.current) return
    const selected = page[nextIndex - pageStart]
    if (!selected) return
    setCurrentIndex(nextIndex)
    setError('')
    try {
      await onNavigate(selected, scope)
    } catch (reason) {
      if (generation !== generationRef.current) return
      setError(reason instanceof Error ? reason.message : 'Не удалось открыть найденное место.')
      onClear()
    }
  }

  const onSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      clearQuery()
    } else if (event.key === 'Enter') {
      event.preventDefault()
      void navigate(currentIndex + (event.shiftKey ? -1 : 1))
    }
  }

  const changeScope = (next: DocumentSearchScope) => {
    if (scope === next) return
    invalidate()
    setLoading(false)
    setMatches([])
    setTotal(0)
    setCurrentIndex(-1)
    setError('')
    onClear()
    onScopeChange(next)
  }

  const status = loading
    ? 'Ищу…'
    : error
      ? error
      : total > 0 && currentIndex >= 0
        ? (currentIndex + 1).toLocaleString('ru-RU') + ' из ' + total.toLocaleString('ru-RU')
        : query.trim()
          ? 'Совпадений нет'
          : 'Введите слово или фразу'

  return (
    <div className="document-search" role="search" aria-label="Поиск в документе">
      <div className="document-search-scopes" aria-label="Область поиска">
        <button type="button" aria-pressed={scope === 'original'} className={scope === 'original' ? 'is-active' : ''} onClick={() => changeScope('original')}>Оригинал</button>
        <button type="button" aria-pressed={scope === 'markdown'} className={scope === 'markdown' ? 'is-active' : ''} onClick={() => changeScope('markdown')}>Markdown</button>
      </div>
      <label className="document-search-input-wrap">
        <Search size={15} aria-hidden="true" />
        <span className="sr-only">Найти в документе</span>
        <input
          type="search"
          value={query}
          onChange={(event) => {
            invalidate()
            setLoading(false)
            setQuery(event.target.value)
            setMatches([])
            setTotal(0)
            setCurrentIndex(-1)
            setError('')
            onClear()
          }}
          onKeyDown={onSearchKeyDown}
          placeholder="Найти в документе"
          aria-label="Найти в документе"
          aria-controls="document-original-viewer"
          aria-keyshortcuts="Enter Shift+Enter Escape"
          autoComplete="off"
        />
        {loading && <LoaderCircle className="spin" size={15} aria-hidden="true" />}
        {query && <button className="document-search-clear" type="button" aria-label="Очистить поиск" title="Очистить поиск" onClick={clearQuery}><X size={14} /></button>}
      </label>
      <span className={'document-search-status' + (error ? ' has-error' : '')} aria-live="polite" aria-atomic="true">{status}</span>
      <div className="document-search-navigation">
        <button type="button" className="icon-button" aria-label="Предыдущее совпадение" title="Предыдущее совпадение · Shift+Enter" disabled={currentIndex <= 0 || loading} onClick={() => void navigate(currentIndex - 1)}><ArrowUp size={15} /></button>
        <button type="button" className="icon-button" aria-label="Следующее совпадение" title="Следующее совпадение · Enter" disabled={currentIndex < 0 || currentIndex >= total - 1 || loading} onClick={() => void navigate(currentIndex + 1)}><ArrowDown size={15} /></button>
      </div>
      <span className="document-search-hint">Enter — далее · Shift+Enter — назад · Esc — очистить</span>
    </div>
  )
}
