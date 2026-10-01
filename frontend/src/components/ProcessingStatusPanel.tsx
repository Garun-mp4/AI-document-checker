import { CircleHelp, LoaderCircle, RotateCw, X } from 'lucide-react'
import type { DocumentStatus, ProcessingJob } from '../types'

function pluralLabel(value: number, one: string, few: string, many: string) {
  const remainder = value % 100
  if (remainder >= 11 && remainder <= 14) return many
  switch (value % 10) {
    case 1: return one
    case 2:
    case 3:
    case 4: return few
    default: return many
  }
}

function formatElapsed(seconds: number) {
  const value = Math.max(0, Math.floor(seconds))
  if (value < 60) return `${value} ${pluralLabel(value, 'секунда', 'секунды', 'секунд')}`
  const minutes = Math.floor(value / 60)
  const remainder = value % 60
  const minuteLabel = pluralLabel(minutes, 'минута', 'минуты', 'минут')
  return remainder ? `${minutes} ${minuteLabel} ${remainder} сек.` : `${minutes} ${minuteLabel}`
}

function progressInfo(job: ProcessingJob | null) {
  if (!job) return null
  const isOcr = job.stage === 'ocr'
  const completed = isOcr ? job.progress.processed_pages : job.progress.completed
  const total = isOcr ? job.progress.total_pages : job.progress.total
  if (typeof completed !== 'number' || typeof total !== 'number' || total <= 0 || completed < 0) return null
  const safeCompleted = Math.min(completed, total)
  const unit = isOcr
    ? pluralLabel(total, 'страница', 'страницы', 'страниц')
    : pluralLabel(total, 'фрагмент', 'фрагмента', 'фрагментов')
  return { completed: safeCompleted, total, unit, text: `${safeCompleted} из ${total} ${unit}` }
}

function estimateRemaining(job: ProcessingJob | null, progress: ReturnType<typeof progressInfo>) {
  if (!job || !progress || progress.completed < 3 || progress.completed >= progress.total
      || typeof job.stage_elapsed_seconds !== 'number' || job.stage_elapsed_seconds < 15) return null
  const midpoint = job.stage_elapsed_seconds * (progress.total - progress.completed) / progress.completed
  if (!Number.isFinite(midpoint) || midpoint <= 0) return null
  const minimum = Math.max(5, Math.floor(midpoint * 0.75 / 5) * 5)
  const maximum = Math.max(minimum + 5, Math.ceil(midpoint * 1.35 / 5) * 5)
  return `Осталось примерно ${formatElapsed(minimum)}–${formatElapsed(maximum)}`
}

function stageLabel(stage: string, status: DocumentStatus | null) {
  if (stage === 'extracting') return 'Читаю документ'
  if (stage === 'ocr') return 'Распознаю страницы'
  if (stage === 'indexing' || stage === 'indexing_checkpoint') return 'Создаю индекс'
  if (stage === 'waiting_analysis') return 'Готовлю ответы'
  if (stage === 'analysis_request') return 'Получаю ответы'
  if (status === 'queued') return 'В очереди'
  if (status === 'ocr') return 'Распознаю страницы'
  if (status === 'indexing') return 'Создаю индекс'
  if (status === 'analyzing') return 'Готовлю ответы'
  return 'Запускаю обработку'
}

export function ProcessingStatusPanel({
  job,
  status,
  fallbackDescription,
  replacementFailure = false,
  actionPending = false,
  onCancel,
  onRetry,
}: {
  job: ProcessingJob | null
  status: DocumentStatus | null
  fallbackDescription: string
  replacementFailure?: boolean
  actionPending?: boolean
  onCancel?: () => void
  onRetry?: () => void
}) {
  const queued = job?.state === 'queued' || (!job && status === 'queued')
  const cancelling = job?.state === 'cancelling'
  const active = queued || job?.state === 'running' || cancelling || (!job && ['extracting', 'ocr', 'indexing', 'analyzing'].includes(status || ''))
  if (!active && !replacementFailure) return null

  const progress = progressInfo(job)
  const estimate = active && job?.state === 'running' ? estimateRemaining(job, progress) : null
  const title = replacementFailure ? 'Не удалось обновить обработку' : cancelling ? 'Останавливаю обработку' : queued ? 'Ожидает обработки' : stageLabel(job?.stage || '', status)
  let description = replacementFailure
    ? 'Предыдущие готовые ответы и история чата сохранены.'
    : queued
      ? job?.queue_position ? `Позиция в очереди: ${job.queue_position}.` : 'Жду свободный слот для обработки.'
      : job?.stage === 'waiting_analysis' ? 'Документ обработан. Жду свободный запрос к модели.'
        : fallbackDescription

  if (queued && job && typeof job.queue_wait_seconds === 'number') {
    description += ` Ожидание: ${formatElapsed(job.queue_wait_seconds)}.`
  } else if (job?.state === 'running' && typeof job.stage_elapsed_seconds === 'number') {
    description += ` Прошло: ${formatElapsed(job.stage_elapsed_seconds)}.`
  }
  if (progress) description += ` ${progress.text}.`
  if (estimate) description += ` ${estimate}.`
  if (replacementFailure && job?.error) description += ` ${job.error}`

  return (
    <section className={`processing-banner ${replacementFailure ? 'processing-banner-failed' : ''}`} aria-label="Статус обработки" data-testid="processing-status">
      <div className="processing-spinner">{replacementFailure ? <CircleHelp size={18} /> : <LoaderCircle size={19} className={cancelling ? undefined : 'spin'} />}</div>
      <div className="processing-copy"><strong aria-live="polite" aria-atomic="true">{title}</strong><span>{description}</span>
        {progress && <>
          <div className="processing-progress-track" role="progressbar" aria-label={`Обработано ${progress.unit}`} aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={progress.completed} aria-valuetext={progress.text}>
            <span style={{ width: `${Math.round(progress.completed / progress.total * 100)}%` }} />
          </div>
        </>}
      </div>
      <div className="processing-actions">
        {queued || job?.state === 'running' ? <button className="button button-light" type="button" onClick={onCancel} disabled={actionPending || !onCancel}><X size={14} />{actionPending ? 'Останавливаю…' : 'Отменить'}</button> : null}
        {replacementFailure && <button className="button button-light" type="button" onClick={onRetry} disabled={actionPending || !onRetry}><RotateCw size={14} />{actionPending ? 'Запускаю…' : 'Повторить'}</button>}
      </div>
    </section>
  )
}
