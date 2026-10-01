import { CircleHelp, Info, RotateCw, RefreshCw } from 'lucide-react'
import { formatBuildTime } from '../buildInfo'
import type { BuildVersionCheck } from '../useBuildVersion'

function shortBuild(value: string) {
  return value.length > 22 ? `${value.slice(0, 10)}…${value.slice(-8)}` : value
}

function describeBuild(check: Extract<BuildVersionCheck, { kind: 'mismatch' | 'unverified' }>) {
  const frontend = check.frontend.build_id
  const api = check.api.build_id
  if (check.kind === 'unverified') {
    return `Интерфейс: ${frontend} · API: ${api}. Соберите приложение через scripts/deploy-compose.ps1, чтобы сверить точную сборку.`
  }
  return `Интерфейс: ${shortBuild(frontend)} · API: ${shortBuild(api)}. Собрано ${formatBuildTime(check.api.built_at)}.`
}

export function BuildVersionNotice({
  check,
  busy,
  onReload,
  onRetry,
}: {
  check: BuildVersionCheck
  busy: boolean
  onReload: () => void
  onRetry: () => void
}) {
  if (check.kind === 'checking' || check.kind === 'matched') return null

  if (check.kind === 'mismatch') {
    return (
      <aside className="build-version-notice" role="alert" aria-live="polite" data-testid="build-version-mismatch">
        <CircleHelp size={17} aria-hidden="true" />
        <div className="build-version-copy">
          <strong>Интерфейс и сервер работают на разных версиях</strong>
          <span>Обновите страницу, чтобы загрузить актуальную сборку. {describeBuild(check)}</span>
        </div>
        <button className="button button-light" type="button" onClick={onReload} disabled={busy} title={busy ? 'Дождитесь завершения текущего ответа или загрузки' : undefined}>
          <RefreshCw size={14} /> Обновить
        </button>
      </aside>
    )
  }

  if (check.kind === 'unverified') {
    return (
      <div className="build-version-indicator" role="status" title={describeBuild(check)}>
        <Info size={14} aria-hidden="true" />
        <span>Версия сборки не подтверждена</span>
        <button className="icon-button" type="button" aria-label="Повторить проверку версии" onClick={onRetry}><RotateCw size={13} /></button>
      </div>
    )
  }

  return (
    <div className="build-version-indicator" role="status">
      <Info size={14} aria-hidden="true" />
      <span>Не удалось проверить версии</span>
      <button className="icon-button" type="button" aria-label="Повторить проверку версии" onClick={onRetry}><RotateCw size={13} /></button>
    </div>
  )
}
