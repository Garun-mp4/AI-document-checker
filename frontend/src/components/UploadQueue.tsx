import { Check, FileText, LoaderCircle, RotateCw, Trash2, X } from 'lucide-react'

export type UploadQueueState = 'queued' | 'uploading' | 'processing' | 'cancelling' | 'ready' | 'failed' | 'cancelled'

export interface UploadQueueEntry {
  id: string
  filename: string
  state: UploadQueueState
  documentId: string | null
  uploadPercent: number | null
  stage: string | null
  progress: Record<string, number>
  error: string | null
  retryMode?: 'retry' | 'replace'
  updatedAt: number
}

interface UploadQueueProps {
  items: UploadQueueEntry[]
  expanded: boolean
  onToggle: () => void
  onOpen: (documentId: string) => void
  onCancel: (item: UploadQueueEntry) => void
  onRetry: (item: UploadQueueEntry) => void
  onDismiss: (itemId: string) => void
  disabled?: boolean
}

const ACTIVE = new Set<UploadQueueState>(['queued', 'uploading', 'processing', 'cancelling'])

function itemStatus(item: UploadQueueEntry): string {
  if (item.state === 'queued') return 'Ожидает отправки'
  if (item.state === 'uploading') return item.uploadPercent === null ? 'Отправляю файл' : `Загружено ${item.uploadPercent}%`
  if (item.state === 'cancelling') return 'Останавливаю обработку'
  if (item.state === 'processing') return item.stage || 'Обрабатывается'
  if (item.state === 'ready') return 'Готово'
  if (item.state === 'cancelled') return 'Отменено'
  return 'Не удалось выполнить'
}

export function UploadQueue({ items, expanded, onToggle, onOpen, onCancel, onRetry, onDismiss, disabled = false }: UploadQueueProps) {
  if (!items.length) return null
  const activeCount = items.filter((item) => ACTIVE.has(item.state)).length
  const heading = activeCount ? `Очередь · ${activeCount} активно` : `Загрузки · ${items.length}`

  return (
    <div className="upload-queue-anchor">
      <button
        type="button"
        className="upload-queue-trigger"
        aria-expanded={expanded}
        aria-controls="upload-queue-panel"
        onClick={onToggle}
      >
        <span className="upload-queue-trigger-icon" aria-hidden="true">
          {activeCount ? <LoaderCircle size={15} className="spin" /> : <FileText size={15} />}
        </span>
        <span>{heading}</span>
        {activeCount > 0 && <span className="upload-queue-count" aria-label={`${activeCount} активных`}>{activeCount}</span>}
      </button>
      {expanded && <section id="upload-queue-panel" className="upload-queue-panel" aria-label="Очередь загрузки документов">
        <header className="upload-queue-heading">
          <div><strong>Загрузка и обработка</strong><span>{activeCount ? `${activeCount} документ${activeCount === 1 ? '' : activeCount < 5 ? 'а' : 'ов'} выполняется` : 'Последние документы'}</span></div>
          <button type="button" className="icon-button" aria-label="Свернуть очередь загрузки" onClick={onToggle}><X size={16} /></button>
        </header>
        <ul className="upload-queue-list">
          {[...items].reverse().map((item) => {
            const active = ACTIVE.has(item.state)
            const uploadProgress = item.state === 'uploading' && item.uploadPercent !== null
            const completedUnits = item.progress.completed ?? item.progress.processed_pages
            const totalUnits = item.progress.total ?? item.progress.total_pages
            const hasWorkProgress = item.state === 'processing' && typeof completedUnits === 'number' && typeof totalUnits === 'number' && totalUnits > 0
            return <li className="upload-queue-item" key={item.id}>
              <div className="upload-queue-item-main">
                <span className={`upload-queue-state-icon is-${item.state}`} aria-hidden="true">
                  {item.state === 'ready' ? <Check size={14} /> : active ? <LoaderCircle size={14} className={item.state === 'cancelling' ? '' : 'spin'} /> : item.state === 'failed' ? <X size={14} /> : <FileText size={14} />}
                </span>
                <div className="upload-queue-item-copy">
                  <strong title={item.filename}>{item.filename}</strong>
                  <span>{itemStatus(item)}</span>
                </div>
              </div>
              {(uploadProgress || hasWorkProgress) && <progress className="upload-queue-progress" max={uploadProgress ? 100 : totalUnits} value={uploadProgress ? item.uploadPercent ?? 0 : completedUnits} aria-label={uploadProgress ? `Загрузка ${item.filename}` : `Обработка ${item.filename}`} />}
              {item.error && <p className="upload-queue-error">{item.error}</p>}
              <div className="upload-queue-actions">
                {item.documentId && <button type="button" className="upload-queue-link" onClick={() => onOpen(item.documentId!)}>Открыть чат</button>}
                {active && <button type="button" className="icon-button" aria-label={item.documentId ? `Отменить обработку ${item.filename}` : `Отменить загрузку ${item.filename}`} title="Отменить" onClick={() => onCancel(item)}><X size={15} /></button>}
                {(item.state === 'failed' || item.state === 'cancelled') && <button type="button" className="icon-button" aria-label={`${item.retryMode === 'replace' ? 'Заменить файл' : 'Повторить'} ${item.filename}`} title={item.retryMode === 'replace' ? 'Выбрать другой файл' : 'Повторить'} disabled={disabled} onClick={() => onRetry(item)}><RotateCw size={15} /></button>}
                {!active && <button type="button" className="icon-button" aria-label={`Убрать ${item.filename} из очереди`} title="Убрать" onClick={() => onDismiss(item.id)}><Trash2 size={14} /></button>}
              </div>
            </li>
          })}
        </ul>
      </section>}
    </div>
  )
}
