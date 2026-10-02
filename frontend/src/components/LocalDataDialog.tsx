import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Archive, Check, Database, Download, Eraser, HardDrive, LoaderCircle, Search, ShieldCheck, Trash2, X } from 'lucide-react'

const API = '/api/v1/maintenance'
const PAGE_SIZE = 20

type MaintenanceAction = 'clear_temp' | 'clear_cache' | 'delete_selected' | 'delete_all'

type Summary = {
  counts: { documents: number; chats: number; messages: number; chunks: number; versions: number }
  storage: {
    originals_bytes: number; markdown_bytes: number; maps_bytes: number; indexes_bytes: number
    parser_cache_bytes: number; embedding_cache_bytes: number; model_cache_bytes: number; cache_entries: number
    temporary_bytes: number; temporary_entries: number
  }
  protection: { codex_authorization: string; active_work: number }
  codex_data_flow: { local: string; sent: string; authorization: string }
}

type LocalDocument = {
  id: string; filename: string; file_type: string; status: string; file_size: number
  markdown_bytes: number; map_bytes: number; chunk_count: number; message_count: number; updated_at: string
}

type DocumentPage = { items: LocalDocument[]; total: number; offset: number; limit: number; has_more: boolean }

type PlanDocument = {
  id: string; filename: string; file_type: string; original_bytes: number; markdown_bytes: number; map_bytes: number
  messages: number; chunks: number; insights: number; versions: number
}

type Plan = {
  plan_id: string; action: MaintenanceAction; expires_at: string; confirmation_phrase: string
  items: number; bytes: number; contents?: Record<string, number>; records?: Record<string, number>
  documents?: PlanDocument[]; documents_truncated?: boolean; scope: string
  model_redownload_required?: boolean
}

type JournalItem = { action: string; outcome: string; item_count: number; bytes_changed: number; created_at: string }

type Props = {
  onClose: () => void
  onDocumentsDeleted: (ids: string[]) => void
  onActionComplete: (message: string) => void
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 Б'
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1 }
  return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: unit === 0 ? 0 : 1 }).format(size)} ${units[unit]}`
}

function actionTitle(action: MaintenanceAction): string {
  return ({ clear_temp: 'Временные файлы', clear_cache: 'Восстанавливаемый кэш',
    delete_selected: 'Выбранные чаты и документы', delete_all: 'Вся библиотека' })[action]
}

function actionVerb(action: MaintenanceAction): string {
  return action === 'clear_temp' || action === 'clear_cache' ? 'Очистить' : 'Удалить'
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    let message = `Ошибка запроса (${response.status})`
    try {
      const body = await response.json() as { detail?: string }
      if (body.detail) message = body.detail
    } catch { /* Keep the status if the response is not JSON. */ }
    throw new Error(message)
  }
  return await response.json() as T
}

export function LocalDataDialog({ onClose, onDocumentsDeleted, onActionComplete }: Props) {
  const dialogRef = useRef<HTMLElement>(null)
  const initialFocusRef = useRef<HTMLButtonElement>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [documents, setDocuments] = useState<DocumentPage | null>(null)
  const [journal, setJournal] = useState<JournalItem[]>([])
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [plan, setPlan] = useState<Plan | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [nextSummary, nextDocuments, nextJournal] = await Promise.all([
        requestJson<Summary>(`${API}/summary`),
        requestJson<DocumentPage>(`${API}/documents?offset=${offset}&limit=${PAGE_SIZE}&q=${encodeURIComponent(debouncedQuery)}`),
        requestJson<JournalItem[]>(`${API}/journal?limit=8`),
      ])
      setSummary(nextSummary)
      setDocuments(nextDocuments)
      setJournal(nextJournal)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Не удалось загрузить локальные данные.')
    } finally {
      setLoading(false)
    }
  }, [debouncedQuery, offset])

  useEffect(() => {
    previousFocusRef.current = window.document.activeElement instanceof HTMLElement ? window.document.activeElement : null
    initialFocusRef.current?.focus()
    return () => previousFocusRef.current?.focus()
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => { setOffset(0); setDebouncedQuery(query.trim()) }, 180)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => { void loadData() }, [loadData])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) { event.preventDefault(); if (plan) setPlan(null); else onClose(); return }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])',
      )].filter((element) => element.offsetParent !== null)
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && window.document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && window.document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.document.addEventListener('keydown', onKeyDown)
    return () => window.document.removeEventListener('keydown', onKeyDown)
  }, [busy, onClose, plan])

  const selectedCount = selected.size
  const selectedOnPage = useMemo(() => documents?.items.filter((item) => selected.has(item.id)).length ?? 0, [documents, selected])

  const startPlan = useCallback(async (action: MaintenanceAction) => {
    setBusy(true)
    setError('')
    try {
      const nextPlan = await requestJson<Plan>(`${API}/plans`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, ...(action === 'delete_selected' ? { document_ids: [...selected] } : {}) }),
      })
      setConfirmation('')
      setPlan(nextPlan)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Не удалось подготовить план.')
    } finally { setBusy(false) }
  }, [selected])

  const executePlan = useCallback(async () => {
    if (!plan || confirmation.trim() !== plan.confirmation_phrase) return
    setBusy(true)
    setError('')
    try {
      const result = await requestJson<{ action: MaintenanceAction; deleted_documents?: number; deleted_bytes?: number; deleted_entries?: number; errors?: number }>(`${API}/execute`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: plan.plan_id, confirmation: confirmation.trim() }),
      })
      const removedIds = plan.action === 'delete_all' || plan.action === 'delete_selected'
        ? (plan.documents ?? []).map((item) => item.id)
        : []
      if (plan.action === 'delete_all' && plan.documents_truncated) {
        // The parent refreshes all library state after an all-library removal.
        onDocumentsDeleted(['*'])
      } else if (removedIds.length) onDocumentsDeleted(removedIds)
      setPlan(null)
      setSelected(new Set())
      setConfirmation('')
      await loadData()
      const verb = plan.action === 'clear_temp' || plan.action === 'clear_cache' ? 'Очищено' : 'Удалено'
      const quantity = result.deleted_documents ?? result.deleted_entries ?? plan.items
      onActionComplete(`${verb}: ${quantity}; освобождено ${formatBytes(result.deleted_bytes ?? plan.bytes)}.`)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Операция не завершена. Обновите предварительный просмотр и повторите.')
      setPlan(null)
      await loadData()
    } finally { setBusy(false) }
  }, [confirmation, loadData, onActionComplete, onDocumentsDeleted, plan])

  const downloadDiagnostics = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`${API}/diagnostics`)
      if (!response.ok) throw new Error(`Не удалось собрать диагностику (${response.status}).`)
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const anchor = window.document.createElement('a')
      anchor.href = url
      anchor.download = 'document-checker-diagnostics.zip'
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Не удалось скачать диагностику.')
    } finally { setBusy(false) }
  }, [])

  const toggleSelected = (id: string) => setSelected((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else if (next.size < 200) next.add(id)
    return next
  })

  const togglePage = () => setSelected((current) => {
    const next = new Set(current)
    const pageItems = documents?.items ?? []
    if (pageItems.length && pageItems.every((item) => next.has(item.id))) pageItems.forEach((item) => next.delete(item.id))
    else pageItems.forEach((item) => { if (next.size < 200) next.add(item.id) })
    return next
  })

  return (
    <div className="modal-backdrop local-data-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) { if (plan) setPlan(null); else onClose() }
    }}>
      <section className="modal-card local-data-dialog" role="dialog" aria-modal="true" aria-labelledby="local-data-title" aria-describedby="local-data-description" aria-busy={busy || loading} ref={dialogRef}>
        <button ref={initialFocusRef} className="icon-button modal-close" type="button" aria-label="Закрыть управление данными" title="Закрыть" onClick={() => { if (plan && !busy) setPlan(null); else if (!busy) onClose() }}><X size={19} /></button>
        <div className="local-data-heading">
          <span className="modal-symbol"><Database size={19} /></span>
          <div>
            <h2 id="local-data-title">Локальные данные</h2>
            <p id="local-data-description">Хранилище приложения на этом компьютере</p>
          </div>
        </div>

        {error && <div className="local-data-error" role="alert">{error}<button type="button" onClick={() => setError('')} aria-label="Скрыть ошибку"><X size={15} /></button></div>}

        {plan ? <div className="local-data-plan" aria-live="polite">
          <button className="local-data-back" type="button" onClick={() => setPlan(null)} disabled={busy}>← Вернуться к данным</button>
          <h3>{actionVerb(plan.action)}: {actionTitle(plan.action)}</h3>
          <p className="local-data-plan-scope">{plan.scope}</p>
          <dl className="local-data-plan-totals">
            <div><dt>Объектов</dt><dd>{plan.items.toLocaleString('ru-RU')}</dd></div>
            <div><dt>Объём файлов</dt><dd>{formatBytes(plan.bytes)}</dd></div>
          </dl>
          {plan.records && <div className="local-data-breakdown">
            <strong>Данные в библиотеке</strong>
            <span>{plan.records.documents ?? 0} документов · {plan.records.originals ?? 0} оригиналов · {plan.records.markdown ?? 0} Markdown · {plan.records.maps ?? 0} карт источников</span>
            <span>{plan.records.chunks ?? 0} фрагментов · {plan.records.insights ?? 0} ответов · {plan.records.messages ?? 0} сообщений · {plan.records.versions ?? 0} версий</span>
          </div>}
          {plan.contents && <div className="local-data-breakdown">
            <strong>Состав очистки</strong>
            {Object.entries(plan.contents).map(([name, count]) => <span key={name}>{name === 'atomic_write' ? 'Незавершённые записи' : name === 'unused_generated_artifact' ? 'Неиспользуемые производные файлы' : name === 'stale_temporary_cache_write' ? 'Незавершённые записи кэша' : name === 'parser_cache_entries' ? 'Кэш парсеров' : name === 'embedding_cache_entries' ? 'Кэш векторов embeddings' : 'Кэш модели embeddings'}: {count.toLocaleString('ru-RU')}</span>)}
          </div>}
          {plan.documents && <div className="local-data-plan-documents" aria-label="Документы, которые будут удалены">
            {plan.documents.slice(0, 20).map((item) => <div key={item.id}><span>{item.filename}</span><small>{item.file_type.toUpperCase()} · оригинал {formatBytes(item.original_bytes)} · чат {item.messages} сообщений</small></div>)}
            {(plan.documents.length > 20 || plan.documents_truncated) && <p>Показаны первые 20 записей. Итоговые счётчики выше учитывают всю библиотеку.</p>}
          </div>}
          {plan.model_redownload_required && <p className="local-data-note">Файлы локальной модели будут удалены. Если она не останется загруженной в памяти, при следующем холодном запуске её придётся скачать повторно.</p>}
          <label className="local-data-confirm-label" htmlFor="local-data-confirm">Для подтверждения введите: <strong>{plan.confirmation_phrase}</strong></label>
          <input id="local-data-confirm" className="local-data-confirm-input" autoComplete="off" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} disabled={busy} />
          <div className="confirm-actions">
            <button className="button button-light" type="button" onClick={() => setPlan(null)} disabled={busy}>Отмена</button>
            <button className="button button-dark" type="button" onClick={() => void executePlan()} disabled={busy || confirmation.trim() !== plan.confirmation_phrase || plan.items === 0}>
              {busy ? <><LoaderCircle className="spin" size={15} /> Выполняю…</> : <><Trash2 size={15} /> {actionVerb(plan.action)} данные</>}
            </button>
          </div>
          <p className="local-data-expiry">План действует 15 минут. Если содержимое успело измениться, операция остановится без удаления и предложит проверить план заново.</p>
        </div> : <>
          <div className="local-data-section-heading"><h3>Что хранится</h3>{loading && <LoaderCircle className="spin" size={15} aria-label="Обновляю данные" />}</div>
          {summary && <>
            <div className="local-data-storage-grid">
              <div><span>Оригиналы</span><strong>{summary.counts.documents} <small>файлов</small></strong><small>{formatBytes(summary.storage.originals_bytes)}</small></div>
              <div><span>Markdown и карты источников</span><strong>{formatBytes(summary.storage.markdown_bytes + summary.storage.maps_bytes)}</strong><small>{formatBytes(summary.storage.markdown_bytes)} Markdown · {formatBytes(summary.storage.maps_bytes)} карты</small></div>
              <div><span>Индекс и фрагменты</span><strong>{summary.counts.chunks.toLocaleString('ru-RU')} <small>фрагментов</small></strong><small>{formatBytes(summary.storage.indexes_bytes)} в базе данных</small></div>
              <div><span>Кэш обработки</span><strong>{formatBytes(summary.storage.parser_cache_bytes + summary.storage.embedding_cache_bytes)}</strong><small>{summary.storage.cache_entries.toLocaleString('ru-RU')} элементов · локальная модель {formatBytes(summary.storage.model_cache_bytes)} сохранена</small></div>
              <div><span>Временные и осиротевшие производные файлы</span><strong>{summary.storage.temporary_entries.toLocaleString('ru-RU')} <small>файлов</small></strong><small>{formatBytes(summary.storage.temporary_bytes)} · удаляются только старше 24 часов</small></div>
              <div><span>Чаты и история</span><strong>{summary.counts.chats.toLocaleString('ru-RU')} <small>чатов</small></strong><small>{summary.counts.messages.toLocaleString('ru-RU')} сообщений</small></div>
            </div>
            <div className="local-data-protection"><ShieldCheck size={16} /><div><strong>Авторизация Codex защищена</strong><span>Очистка кэша и библиотеки не затрагивает вход в аккаунт.</span></div></div>
          </>}

          <div className="local-data-actions">
            <button className="button button-light" type="button" onClick={() => void startPlan('clear_temp')} disabled={busy || loading || !summary?.storage.temporary_entries}>
              <Eraser size={15} /> Очистить временные файлы <span>{formatBytes(summary?.storage.temporary_bytes ?? 0)}</span>
            </button>
            <button className="button button-light" type="button" onClick={() => void startPlan('clear_cache')} disabled={busy || loading || !summary?.storage.cache_entries}>
              <HardDrive size={15} /> Очистить кэш <span>{formatBytes((summary?.storage.parser_cache_bytes ?? 0) + (summary?.storage.embedding_cache_bytes ?? 0))}</span>
            </button>
          </div>

          <div className="local-data-section-heading local-data-library-heading"><h3>Библиотека документов и чатов</h3><span>{documents?.total ?? 0}</span></div>
          <div className="local-data-library-tools">
            <label className="local-data-search"><Search size={15} /><input aria-label="Найти документ" placeholder="Найти документ" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
            <button className="button button-light" type="button" onClick={togglePage} disabled={!documents?.items.length || busy}>
              {selectedOnPage === documents?.items.length && selectedOnPage > 0 ? 'Снять выбор страницы' : 'Выбрать страницу'}
            </button>
          </div>
          <div className="local-data-document-list" aria-busy={loading}>
            {loading && !documents && <div className="local-data-loading">Загружаю библиотеку…</div>}
            {documents?.items.map((item) => <label className="local-data-document-row" key={item.id}>
              <input type="checkbox" checked={selected.has(item.id)} onChange={() => toggleSelected(item.id)} disabled={busy || (!selected.has(item.id) && selected.size >= 200)} aria-label={`Выбрать ${item.filename}`} />
              <span className="local-data-file-icon"><Archive size={16} /></span>
              <span className="local-data-document-copy"><strong title={item.filename}>{item.filename}</strong><small>{item.file_type.toUpperCase()} · оригинал {formatBytes(item.file_size)} · {item.message_count} сообщ. · {item.chunk_count} фрагм.</small></span>
            </label>)}
            {!loading && documents?.items.length === 0 && <p className="local-data-empty">{debouncedQuery ? 'Документы не найдены.' : 'Библиотека пока пуста.'}</p>}
          </div>
          {documents && <div className="local-data-pagination">
            <span>{documents.total ? `${documents.offset + 1}–${Math.min(documents.offset + documents.items.length, documents.total)} из ${documents.total}` : '0 документов'}{selected.size >= 200 ? ' · максимум 200 за один выбор' : ''}</span>
            <div><button type="button" className="button button-light" onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))} disabled={!offset || busy}>Назад</button><button type="button" className="button button-light" onClick={() => setOffset((value) => value + PAGE_SIZE)} disabled={!documents.has_more || busy}>Дальше</button></div>
          </div>}
          <div className="local-data-delete-actions">
            <button className="button button-light" type="button" onClick={() => void startPlan('delete_selected')} disabled={busy || selectedCount === 0 || selectedCount > 200}>
              <Trash2 size={15} /> Удалить выбранные <span>{selectedCount}</span>
            </button>
            <button className="local-data-delete-all" type="button" onClick={() => void startPlan('delete_all')} disabled={busy || loading || !summary?.counts.documents}>
              Удалить всю библиотеку
            </button>
          </div>

          {summary && <section className="local-data-privacy" aria-labelledby="local-data-privacy-title">
            <h3 id="local-data-privacy-title">Какие данные получает Codex</h3>
            <p>{summary.codex_data_flow.sent}</p>
            <p>{summary.codex_data_flow.local} {summary.codex_data_flow.authorization}</p>
          </section>}

          <section className="local-data-diagnostics" aria-labelledby="local-data-diagnostics-title">
            <div><h3 id="local-data-diagnostics-title">Диагностика и резервная копия</h3><p>В диагностике нет текста документов, переписки, имён файлов, путей и секретов.</p></div>
            <button className="button button-light" type="button" onClick={() => void downloadDiagnostics()} disabled={busy}><Download size={15} /> Скачать диагностику</button>
          </section>

          <details className="local-data-backup-help">
            <summary>Резервное копирование и восстановление</summary>
            <p>Рабочие данные находятся в Docker volumes базы и оригиналов; настройки входа Codex лежат отдельно. Перед обслуживанием создавайте согласованную копию PostgreSQL и тома документов. Не используйте <code>docker compose down -v</code> для рабочей установки.</p>
            <p>Пошаговые команды и проверка восстановления на изолированном проекте: <code>docs/M15_LOCAL_DATA.md</code> в репозитории.</p>
          </details>

          <section className="local-data-journal" aria-labelledby="local-data-journal-title">
            <h3 id="local-data-journal-title">Последние операции</h3>
            {!journal.length && <p>Операций обслуживания пока нет.</p>}
            {journal.map((item, index) => <div key={`${item.created_at}-${index}`}><span>{item.action === 'clear_temp' ? 'Временные файлы' : item.action === 'clear_cache' ? 'Кэш' : 'Удаление документов'} · {item.outcome === 'complete' ? 'завершено' : 'частично'}</span><small>{new Date(item.created_at).toLocaleString('ru-RU')} · {item.item_count} объектов · {formatBytes(item.bytes_changed)}</small></div>)}
          </section>
          <div className="local-data-footer">
            <button className="button button-light" type="button" onClick={() => void loadData()} disabled={loading || busy}><LoaderCircle className={loading ? 'spin' : ''} size={15} /> Обновить сведения</button>
            <span>{summary?.protection.active_work ? `${summary.protection.active_work} активных заданий защищены` : <><Check size={14} /> Активные данные не удаляются</>}</span>
          </div>
        </>}
      </section>
    </div>
  )
}
