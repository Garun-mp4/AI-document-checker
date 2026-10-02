import { useCallback, useRef, type RefCallback } from 'react'

export const APP_HELP_CATALOG_VERSION = '1'

export const APP_HELP_UI_TARGET_IDS = [
  'document.upload.open',
  'chat-library.toggle',
  'chat.new',
  'codex.settings.open',
  'workspace.layout.select',
  'document.original.open',
  'document.markdown.open',
  'document.search.open',
  'document.ocr.settings',
  'document.citations.open',
  'document.bookmarks.open',
  'document.export.open',
  'local-data.open',
  'document.table.controls',
] as const

export type AppHelpUiTargetId = typeof APP_HELP_UI_TARGET_IDS[number]

export const APP_HELP_UI_TARGET_LABELS: Record<AppHelpUiTargetId, string> = {
  'document.upload.open': 'Загрузка документа',
  'chat-library.toggle': 'Библиотека чатов',
  'chat.new': 'Новый чат',
  'codex.settings.open': 'Подключение Codex',
  'workspace.layout.select': 'Расположение документа',
  'document.original.open': 'Вкладка «Оригинал»',
  'document.markdown.open': 'Вкладка «Markdown»',
  'document.search.open': 'Поиск в документе',
  'document.ocr.settings': 'Настройки OCR',
  'document.citations.open': 'Источники ответа',
  'document.bookmarks.open': 'Закладки',
  'document.export.open': 'Экспорт',
  'local-data.open': 'Локальные данные',
  'document.table.controls': 'Управление таблицей',
}

const allowedTargets = new Set<string>(APP_HELP_UI_TARGET_IDS)
const registeredTargets = new Map<AppHelpUiTargetId, Set<HTMLElement>>()
const highlightTimeouts = new WeakMap<HTMLElement, number>()

export function isAppHelpUiTargetId(value: unknown): value is AppHelpUiTargetId {
  return typeof value === 'string' && allowedTargets.has(value)
}

export function useAppHelpTargetRef(targetId: AppHelpUiTargetId): RefCallback<HTMLElement> {
  const currentElement = useRef<HTMLElement | null>(null)
  return useCallback((element) => {
    const previous = currentElement.current
    if (previous && previous !== element) {
      const existing = registeredTargets.get(targetId)
      existing?.delete(previous)
      if (existing?.size === 0) registeredTargets.delete(targetId)
    }

    currentElement.current = element
    if (element) {
      element.dataset.helpTarget = targetId
      const targets = registeredTargets.get(targetId) ?? new Set<HTMLElement>()
      targets.add(element)
      registeredTargets.set(targetId, targets)
    }
  }, [targetId])
}

export type AppHelpTargetUnavailableReason = 'unknown' | 'missing' | 'hidden' | 'disabled' | 'loading' | 'covered' | 'ambiguous'

export type AppHelpTargetResolution =
  | { available: true; element: HTMLElement }
  | { available: false; reason: AppHelpTargetUnavailableReason }

function elementUnavailableReason(element: HTMLElement, targetId: AppHelpUiTargetId): AppHelpTargetUnavailableReason | null {
  const doc = element.ownerDocument
  const view = doc.defaultView
  if (!element.isConnected || element.dataset.helpTarget !== targetId || !view) return 'missing'

  for (let ancestor: HTMLElement | null = element; ancestor; ancestor = ancestor.parentElement) {
    if (ancestor.hidden || ancestor.hasAttribute('inert') || ancestor.getAttribute('aria-hidden') === 'true') return 'hidden'
    if (ancestor.hasAttribute('disabled') || ancestor.getAttribute('aria-disabled') === 'true' || ancestor.matches(':disabled')) return 'disabled'
    if (ancestor.getAttribute('aria-busy') === 'true' || ancestor.dataset.loading === 'true') return 'loading'
    const style = view.getComputedStyle(ancestor)
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return 'hidden'
  }

  const rect = element.getBoundingClientRect()
  if (!element.getClientRects().length || rect.width <= 0 || rect.height <= 0) return 'hidden'
  const width = view.innerWidth || doc.documentElement.clientWidth
  const centerX = rect.left + rect.width / 2
  if (centerX <= 0 || centerX >= width) return 'hidden'

  const height = view.innerHeight || doc.documentElement.clientHeight
  const centerY = rect.top + rect.height / 2
  if (centerY >= 0 && centerY < height) {
    const coveringElement = doc.elementFromPoint(centerX, centerY)
    if (coveringElement && coveringElement !== element && !element.contains(coveringElement)) return 'covered'
  }

  return null
}

export function resolveAppHelpTarget(value: unknown): AppHelpTargetResolution {
  if (!isAppHelpUiTargetId(value)) return { available: false, reason: 'unknown' }
  const candidates = [...(registeredTargets.get(value) ?? [])]
  const available = candidates.filter((element) => elementUnavailableReason(element, value) === null)
  if (available.length > 1) return { available: false, reason: 'ambiguous' }
  if (available.length === 1) return { available: true, element: available[0] }
  const reason = candidates.map((element) => elementUnavailableReason(element, value)).find(Boolean)
  return { available: false, reason: reason ?? 'missing' }
}

export function highlightAppHelpTarget(element: HTMLElement, durationMs = 1_800): void {
  const previousTimeout = highlightTimeouts.get(element)
  if (previousTimeout !== undefined) window.clearTimeout(previousTimeout)
  element.setAttribute('data-help-highlighted', 'true')
  const timeout = window.setTimeout(() => {
    element.removeAttribute('data-help-highlighted')
    highlightTimeouts.delete(element)
  }, durationMs)
  highlightTimeouts.set(element, timeout)
}
