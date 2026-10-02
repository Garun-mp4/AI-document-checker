import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlignLeft,
  ArrowUp,
  BookOpen,
  Calculator,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Clock3,
  Download,
  FileCode2,
  FileSpreadsheet,
  FileText,
  FileUp,
  LoaderCircle,
  Maximize2,
  MoreHorizontal,
  Menu,
  MessageSquareText,
  Minimize2,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  Pencil,
  Pin,
  PinOff,
  Search,
  RotateCw,
  Send,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react'
import { createPortal } from 'react-dom'
import type { ChatLibraryPage, ChatMessage, ChatRecord, ChatSettings, ChatSummary, CodexStatus, DocumentPreview, DocumentRecord, DocumentSearchMatch, DocumentSearchScope, Insight, MarkdownDocument, ProcessingJob, SourceRef, StreamCitation } from './types'
import { OriginalDocumentViewer } from './components/OriginalDocumentViewer'
import type { OcrReprocessOptions } from './components/OriginalDocumentViewer'
import { MarkdownViewer } from './components/MarkdownViewer'
import { DocumentSearchToolbar } from './components/DocumentSearchToolbar'
import { ChatMarkdown } from './components/ChatMarkdown'
import { ProcessingStatusPanel } from './components/ProcessingStatusPanel'
import { BuildVersionNotice } from './components/BuildVersionNotice'
import { useBuildVersion } from './useBuildVersion'

const API = '/api/v1'
const ACCEPTED = '.pdf,.docx,.txt,.md,.csv,.xml,.xlsx,.xls,.pptx,.html,.htm,.json,.epub'
const SELECTED_CHAT_STORAGE_KEY = 'document-checker-selected-chat'
const NEW_CHAT_STORAGE_KEY = 'document-checker-new-chat'
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'document-checker-sidebar-collapsed'
const DEFAULT_CODEX_MODEL = 'gpt-6-luna'
const DEFAULT_CODEX_REASONING = 'medium'
const ALLOWED_CODEX_MODELS = new Set(['gpt-6-luna', 'gpt-6.1-sol'])
const CHAT_LIBRARY_PAGE_SIZE = 30

type ChatLibraryActionMenu = { chatId: string; top: number; left: number }

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    let message = `Ошибка запроса (${response.status})`
    try {
      const body = await response.json() as { detail?: string }
      if (body.detail) message = body.detail
    } catch {
      // Keep the HTTP status as a useful fallback for non-JSON proxy errors.
    }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return await response.json() as T
}

function statusLabel(status: DocumentRecord['status']): string {
  return {
    queued: 'В очереди',
    extracting: 'Читаю документ',
    ocr: 'Распознаю скан',
    indexing: 'Создаю индекс',
    analyzing: 'Готовлю ответы',
    cancelled: 'Обработка отменена',
    ready: 'Готово',
    needs_auth: 'Нужен вход Codex',
    model_unavailable: 'Модель недоступна',
    error: 'Ошибка обработки',
  }[status]
}

function locatorText(source: { locator: SourceRef['locator'] }): string {
  const label = source.locator.label
  if (typeof label === 'string' && label) return label
  const page = source.locator.page
  if (typeof page === 'number') return `Страница ${page}`
  const lineStart = source.locator.line_start
  const lineEnd = source.locator.line_end
  if (typeof lineStart === 'number') return `Строки ${lineStart}–${typeof lineEnd === 'number' ? lineEnd : lineStart}`
  const slide = source.locator.slide
  if (typeof slide === 'number') return `Слайд ${slide}`
  const sheet = source.locator.sheet
  const row = source.locator.row
  if (typeof sheet === 'string' && typeof row === 'number') return `Лист «${sheet}», строка ${row}`
  return 'Фрагмент документа'
}

function fileIcon(fileType: string, size = 18) {
  if (['csv', 'xlsx', 'xls'].includes(fileType)) return <FileSpreadsheet size={size} strokeWidth={1.7} />
  if (['xml', 'json', 'html', 'htm'].includes(fileType)) return <FileCode2 size={size} strokeWidth={1.7} />
  return <FileText size={size} strokeWidth={1.7} />
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`
  return `${(bytes / 1024 / 1024).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} МБ`
}

function codexModelLabel(status: CodexStatus | null): string {
  if (status?.model_label) return status.model_label
  const model = status?.model || DEFAULT_CODEX_MODEL
  return model.replace(/^gpt(?=-)/i, 'GPT').replace(/(?<=\d)-(?=[a-z])/gi, ' ').replace(/(?<=\s)([a-z])/g, (letter) => letter.toUpperCase())
}

function codexReasoningLabel(value: string | undefined): string {
  const normalized = (value || DEFAULT_CODEX_REASONING).toLowerCase()
  return normalized === 'xhigh' ? 'Xhigh' : normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function pluralLabel(value: number, one: string, few: string, many = few): string {
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

function relativeDate(value: string): string {
  const elapsed = Date.now() - new Date(value).getTime()
  const hours = Math.floor(elapsed / 3_600_000)
  if (hours < 1) return 'только что'
  if (hours < 24) return `${hours} ч. назад`
  const days = Math.floor(hours / 24)
  return days === 1 ? 'вчера' : `${days} дн. назад`
}

function scrollIntoViewRespectingMotion(target: Element | null, block: ScrollLogicalPosition = 'start') {
  if (!target) return
  const reduceMotion = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block })
}

function summaryToDocument(summary: ChatSummary): DocumentRecord {
  return {
    active_version: 0,
    id: summary.document_id,
    filename: summary.filename,
    file_type: summary.file_type,
    file_size: summary.file_size,
    status: summary.status,
    error_message: summary.error_message,
    chunk_count: summary.chunk_count,
    metadata: summary.metadata,
    markdown_status: 'legacy',
    analysis_source: 'native_fallback',
    markdown_error: null,
    markdown_converter_version: null,
    markdown_char_count: 0,
    markdown_line_count: 0,
    markdown_checksum: null,
    markdown_mapping: {},
    ocr_status: 'not_needed',
    ocr_language: null,
    ocr_page_count: null,
    ocr_confidence: null,
    ocr_error: null,
    ocr_engine_version: null,
    ocr_char_count: 0,
    created_at: summary.created_at,
    updated_at: summary.last_activity_at,
  }
}

function App() {
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [chatLibraryTotal, setChatLibraryTotal] = useState(0)
  const [chatLibraryHasMore, setChatLibraryHasMore] = useState(false)
  const [chatLibraryLoading, setChatLibraryLoading] = useState(false)
  const [chatLibraryLoadingMore, setChatLibraryLoadingMore] = useState(false)
  const [chatLibraryError, setChatLibraryError] = useState('')
  const [chatSearch, setChatSearch] = useState('')
  const [debouncedChatSearch, setDebouncedChatSearch] = useState('')
  const [chatActionMenu, setChatActionMenu] = useState<ChatLibraryActionMenu | null>(null)
  const [renameTargetId, setRenameTargetId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [chatSettingsPendingId, setChatSettingsPendingId] = useState<string | null>(null)
  const [renameError, setRenameError] = useState('')
  const [pendingMessageNavigation, setPendingMessageNavigation] = useState<string | null>(null)
  const [highlightedMessageId, setHighlightedMessageId] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(() => localStorage.getItem(NEW_CHAT_STORAGE_KEY) === 'true' ? null : localStorage.getItem(SELECTED_CHAT_STORAGE_KEY))
  const [newChatOpen, setNewChatOpen] = useState(() => localStorage.getItem(NEW_CHAT_STORAGE_KEY) === 'true')
  const [document, setDocument] = useState<DocumentRecord | null>(null)
  const [processingJob, setProcessingJob] = useState<ProcessingJob | null>(null)
  const [processingActionPending, setProcessingActionPending] = useState(false)
  const [insights, setInsights] = useState<Insight[]>([])
  const [chat, setChat] = useState<ChatRecord | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [codex, setCodex] = useState<CodexStatus | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadActive, setUploadActive] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(true)
  const [chatOpen, setChatOpen] = useState(true)
  const [chatFull, setChatFull] = useState(false)
  const [chatWidth, setChatWidth] = useState(() => Number(localStorage.getItem('document-checker-chat-width')) || 360)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true')
  const [mobileLibraryOpen, setMobileLibraryOpen] = useState(false)
  const [mobileChatOpen, setMobileChatOpen] = useState(false)
  const [compactChatLayout, setCompactChatLayout] = useState(() => window.matchMedia('(max-width: 959px)').matches)
  useEffect(() => {
    const query = window.matchMedia('(max-width: 959px)')
    const update = () => setCompactChatLayout(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  const chatVisible = chatOpen && (!compactChatLayout || mobileChatOpen || chatFull)
  const toggleChat = () => {
    const nextVisible = !chatVisible
    setChatOpen(nextVisible)
    setMobileChatOpen(compactChatLayout && nextVisible)
    setChatFull(false)
    if (nextVisible) setMobileLibraryOpen(false)
  }
  const [authOpen, setAuthOpen] = useState(false)
  const [openPreferenceMenu, setOpenPreferenceMenu] = useState<'model' | 'reasoning' | null>(null)
  const [codexSaving, setCodexSaving] = useState(false)
  const [codexPreferenceMessage, setCodexPreferenceMessage] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<DocumentRecord | null>(null)
  const [chatInput, setChatInput] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [streamText, setStreamText] = useState('')
  const [streamSources, setStreamSources] = useState<StreamCitation[]>([])
  const [toast, setToast] = useState('')
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null)
  const [selectedSource, setSelectedSource] = useState<SourceRef | StreamCitation | null>(null)
  const [searchScope, setSearchScope] = useState<DocumentSearchScope>('original')
  const [searchSelection, setSearchSelection] = useState<DocumentSearchMatch | null>(null)
  const [searchResetKey, setSearchResetKey] = useState(0)
  const [documentPreview, setDocumentPreview] = useState<DocumentPreview | null>(null)
  const [markdownDocument, setMarkdownDocument] = useState<MarkdownDocument | null>(null)
  const [previewTab, setPreviewTab] = useState<'original' | 'markdown'>('original')
  const [markdownRebuilding, setMarkdownRebuilding] = useState(false)
  const [markdownLoadingMore, setMarkdownLoadingMore] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [exportScope, setExportScope] = useState<'analysis' | 'selected_answers' | 'conversation'>('analysis')
  const [exportFormat, setExportFormat] = useState<'markdown' | 'pdf'>('pdf')
  const [exportSelectedKeys, setExportSelectedKeys] = useState<string[]>([])
  const [exportPending, setExportPending] = useState(false)
  const [exportError, setExportError] = useState('')
  const [previewPage, setPreviewPage] = useState(1)
  const { check: buildVersionCheck, recheck: recheckBuildVersion } = useBuildVersion()
  const fileInput = useRef<HTMLInputElement>(null)
  const librarySearchRef = useRef<HTMLInputElement>(null)
  const chatMenuRef = useRef<HTMLDivElement>(null)
  const chatMenuTriggerRef = useRef<HTMLButtonElement | null>(null)
  const chatInputRef = useRef<HTMLTextAreaElement>(null)
  const workArea = useRef<HTMLElement>(null)
  const conversation = useRef<HTMLDivElement>(null)
  const authDetailsRef = useRef<HTMLDivElement>(null)
  const modelTriggerRef = useRef<HTMLButtonElement>(null)
  const reasoningTriggerRef = useRef<HTMLButtonElement>(null)
  const exportTriggerRef = useRef<HTMLButtonElement>(null)
  const resizeState = useRef<{ startX: number; startWidth: number } | null>(null)
  const searchNavigationRef = useRef(0)
  const skipNextChatAutoScrollRef = useRef(false)
  const chatLibraryRequestRef = useRef(0)
  const selectedIdRef = useRef(selectedId)
  const documentRef = useRef(document)
  const markdownDocumentRef = useRef(markdownDocument)
  selectedIdRef.current = selectedId
  documentRef.current = document
  markdownDocumentRef.current = markdownDocument

  const showToast = useCallback((message: string) => {
    setToast(message)
    window.setTimeout(() => setToast(''), 4_500)
  }, [])

  const closeExportDialog = useCallback(() => {
    if (exportPending) return
    setExportOpen(false)
    window.requestAnimationFrame(() => exportTriggerRef.current?.focus())
  }, [exportPending])

  const startExport = useCallback(() => {
    setExportScope('analysis')
    setExportSelectedKeys(insights.map((insight) => insight.key))
    setExportError('')
    setExportOpen(true)
  }, [insights])

  const downloadExport = useCallback(async () => {
    if (!document || exportPending) return
    setExportPending(true)
    setExportError('')
    try {
      const response = await fetch(`${API}/documents/${document.id}/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: exportScope,
          format: exportFormat,
          selected_keys: exportScope === 'selected_answers' ? exportSelectedKeys : [],
        }),
      })
      if (!response.ok) {
        let message = `Не удалось подготовить экспорт (${response.status}).`
        try {
          const errorBody = await response.json() as { detail?: string }
          if (errorBody.detail) message = errorBody.detail
        } catch {
          // Keep the status-based message if the proxy returned a non-JSON body.
        }
        throw new Error(message)
      }
      const expectedType = exportFormat === 'pdf' ? 'application/pdf' : 'text/markdown'
      if (!response.headers.get('content-type')?.includes(expectedType)) {
        throw new Error('Сервер вернул файл в неожиданном формате. Повторите экспорт.')
      }
      const blob = await response.blob()
      const objectUrl = URL.createObjectURL(blob)
      const anchor = window.document.createElement('a')
      const extension = exportFormat === 'pdf' ? '.pdf' : '.md'
      const filename = document.filename.replace(/\.[^.]+$/, '') + extension
      anchor.href = objectUrl
      anchor.download = filename
      anchor.hidden = true
      window.document.body.append(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000)
      setExportOpen(false)
      window.requestAnimationFrame(() => exportTriggerRef.current?.focus())
    } catch (error) {
      setExportError(error instanceof Error ? error.message : 'Не удалось подготовить экспорт. Повторите попытку.')
    } finally {
      setExportPending(false)
    }
  }, [document, exportFormat, exportPending, exportScope, exportSelectedKeys])

  const updateDocumentInLibrary = useCallback((record: DocumentRecord) => {
    setChats((current) => current.map((item) => item.document_id === record.id ? {
      ...item,
      filename: record.filename,
      file_type: record.file_type,
      file_size: record.file_size,
      status: record.status,
      error_message: record.error_message,
      chunk_count: record.chunk_count,
      metadata: record.metadata,
      last_activity_at: item.message_count ? item.last_activity_at : record.updated_at,
    } : item))
  }, [])

  const refreshLibrary = useCallback(async (preferredDocumentId?: string) => {
    const requestId = ++chatLibraryRequestRef.current
    setChatLibraryLoading(true)
    setChatLibraryError('')
    try {
      const params = new URLSearchParams({ offset: '0', limit: String(CHAT_LIBRARY_PAGE_SIZE) })
      if (debouncedChatSearch) params.set('q', debouncedChatSearch)
      const result = await api<ChatLibraryPage>(`${API}/chats/library?${params}`)
      if (requestId !== chatLibraryRequestRef.current) return
      setChats(result.items)
      setChatLibraryTotal(result.total)
      setChatLibraryHasMore(result.has_more)
      setSelectedId((current) => preferredDocumentId ?? (newChatOpen ? null : current ?? result.items[0]?.document_id ?? null))
    } catch (error) {
      if (requestId === chatLibraryRequestRef.current) {
        setChatLibraryError(error instanceof Error ? error.message : 'Не удалось загрузить чаты.')
      }
    } finally {
      if (requestId === chatLibraryRequestRef.current) setChatLibraryLoading(false)
    }
  }, [debouncedChatSearch, newChatOpen])

  useEffect(() => {
    void refreshLibrary()
  }, [refreshLibrary])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedChatSearch(chatSearch.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [chatSearch])

  useEffect(() => {
    const refreshWhenVisible = () => {
      if (window.document.visibilityState === 'visible') void refreshLibrary()
    }
    window.document.addEventListener('visibilitychange', refreshWhenVisible)
    return () => window.document.removeEventListener('visibilitychange', refreshWhenVisible)
  }, [refreshLibrary])

  const loadMoreChats = useCallback(async () => {
    if (!chatLibraryHasMore || chatLibraryLoadingMore || chatSearch.trim() !== debouncedChatSearch) return
    const requestId = chatLibraryRequestRef.current
    setChatLibraryLoadingMore(true)
    setChatLibraryError('')
    try {
      const params = new URLSearchParams({ offset: String(chats.length), limit: String(CHAT_LIBRARY_PAGE_SIZE) })
      if (debouncedChatSearch) params.set('q', debouncedChatSearch)
      const result = await api<ChatLibraryPage>(`${API}/chats/library?${params}`)
      if (requestId !== chatLibraryRequestRef.current) return
      setChats((current) => {
        const knownIds = new Set(current.map((item) => item.id))
        return [...current, ...result.items.filter((item) => !knownIds.has(item.id))]
      })
      setChatLibraryTotal(result.total)
      setChatLibraryHasMore(result.has_more)
    } catch (error) {
      if (requestId === chatLibraryRequestRef.current) {
        setChatLibraryError(error instanceof Error ? error.message : 'Не удалось загрузить следующую страницу чатов.')
      }
    } finally {
      setChatLibraryLoadingMore(false)
    }
  }, [chatLibraryHasMore, chatLibraryLoadingMore, chatSearch, debouncedChatSearch, chats.length])

  const selectLibraryChat = useCallback((item: ChatSummary) => {
    setPendingMessageNavigation(item.search_message_id)
    setHighlightedMessageId(null)
    setNewChatOpen(false)
    setSelectedId(item.document_id)
    setMobileLibraryOpen(false)
    if (selectedId === item.document_id && item.search_message_id) {
      void api<ChatMessage[]>(`${API}/chats/${item.id}/messages`)
        .then(setMessages)
        .catch((error: unknown) => showToast(error instanceof Error ? error.message : 'Не удалось открыть найденное сообщение.'))
    }
  }, [selectedId, showToast])

  const openChatActionMenu = useCallback((event: React.MouseEvent<HTMLButtonElement>, item: ChatSummary) => {
    event.stopPropagation()
    const rect = event.currentTarget.getBoundingClientRect()
    const menuWidth = 220
    const menuHeight = item.custom_title ? 190 : 150
    setChatActionMenu({
      chatId: item.id,
      left: Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8)),
      top: Math.max(8, Math.min(rect.bottom + 5, window.innerHeight - menuHeight - 8)),
    })
    chatMenuTriggerRef.current = event.currentTarget
    window.requestAnimationFrame(() => chatMenuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus())
  }, [])

  const closeChatActionMenu = useCallback((restoreFocus = false) => {
    setChatActionMenu(null)
    if (restoreFocus) window.requestAnimationFrame(() => chatMenuTriggerRef.current?.focus())
  }, [])

  useEffect(() => {
    if (!chatActionMenu) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (!chatMenuRef.current?.contains(target) && !chatMenuTriggerRef.current?.contains(target)) {
        setChatActionMenu(null)
      }
    }
    window.document.addEventListener('pointerdown', onPointerDown)
    return () => window.document.removeEventListener('pointerdown', onPointerDown)
  }, [chatActionMenu])

  const beginChatRename = useCallback((item: ChatSummary) => {
    setSidebarCollapsed(false)
    setRenameTargetId(item.id)
    setRenameDraft(item.custom_title ?? item.title)
    setRenameError('')
    closeChatActionMenu()
  }, [closeChatActionMenu])

  const updateChatSettings = useCallback(async (
    item: ChatSummary,
    changes: { title?: string | null; pinned?: boolean },
  ): Promise<ChatSettings> => {
    const current = chats.find((chatItem) => chatItem.id === item.id) ?? item
    return await api<ChatSettings>(`${API}/chats/${item.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_revision: current.revision, ...changes }),
    })
  }, [chats])

  const toggleChatPin = useCallback(async (item: ChatSummary) => {
    closeChatActionMenu()
    setChatSettingsPendingId(item.id)
    try {
      await updateChatSettings(item, { pinned: !item.pinned })
      await refreshLibrary()
      showToast(item.pinned ? 'Чат откреплён.' : 'Чат закреплён.')
    } catch (error) {
      if (error instanceof Error && error.message.includes('изменился в другой вкладке')) await refreshLibrary()
      showToast(error instanceof Error ? error.message : 'Не удалось изменить закрепление чата.')
    } finally {
      setChatSettingsPendingId(null)
    }
  }, [closeChatActionMenu, refreshLibrary, showToast, updateChatSettings])

  const saveChatRename = useCallback(async (item: ChatSummary) => {
    const title = renameDraft.trim()
    if (!title) {
      setRenameError('Введите название чата.')
      return
    }
    setChatSettingsPendingId(item.id)
    setRenameError('')
    try {
      await updateChatSettings(item, { title })
      setRenameTargetId(null)
      await refreshLibrary()
      showToast('Название чата сохранено.')
    } catch (error) {
      if (error instanceof Error && error.message.includes('изменился в другой вкладке')) await refreshLibrary()
      setRenameError(error instanceof Error ? error.message : 'Не удалось сохранить название чата.')
    } finally {
      setChatSettingsPendingId(null)
    }
  }, [refreshLibrary, renameDraft, showToast, updateChatSettings])

  const resetChatTitle = useCallback(async (item: ChatSummary) => {
    closeChatActionMenu()
    setChatSettingsPendingId(item.id)
    try {
      await updateChatSettings(item, { title: null })
      await refreshLibrary()
      showToast('Возвращено исходное название чата.')
    } catch (error) {
      if (error instanceof Error && error.message.includes('изменился в другой вкладке')) await refreshLibrary()
      showToast(error instanceof Error ? error.message : 'Не удалось вернуть исходное название.')
    } finally {
      setChatSettingsPendingId(null)
    }
  }, [closeChatActionMenu, refreshLibrary, showToast, updateChatSettings])

  const clearChatRename = useCallback(() => {
    setRenameTargetId(null)
    setRenameDraft('')
    setRenameError('')
  }, [])

  const handleChatMenuKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'))
    if (!items.length) return
    const current = items.indexOf(window.document.activeElement as HTMLButtonElement)
    let next = current
    if (event.key === 'ArrowDown') next = (current + 1) % items.length
    else if (event.key === 'ArrowUp') next = (current - 1 + items.length) % items.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = items.length - 1
    else return
    event.preventDefault()
    items[next].focus()
  }, [])

  const focusLibrarySearch = useCallback(() => {
    setSidebarCollapsed(false)
    window.requestAnimationFrame(() => librarySearchRef.current?.focus())
  }, [])

  useEffect(() => {
    if (selectedId) localStorage.setItem(SELECTED_CHAT_STORAGE_KEY, selectedId)
    else localStorage.removeItem(SELECTED_CHAT_STORAGE_KEY)
  }, [selectedId])

  useEffect(() => {
    localStorage.setItem(NEW_CHAT_STORAGE_KEY, String(newChatOpen))
  }, [newChatOpen])

  useEffect(() => {
    let active = true
    const refreshStatus = async () => {
      try {
        const result = await api<CodexStatus>(`${API}/codex/status`)
        if (active) setCodex(result)
      } catch {
        if (active) setCodex(null)
      }
    }
    void refreshStatus()
    const timer = window.setInterval(() => void refreshStatus(), 4_000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [])

  const loadReadyData = useCallback(async (documentId: string) => {
    try {
      const [cardData, chatData, previewData, markdownData] = await Promise.all([
        api<Insight[]>(`${API}/documents/${documentId}/insights`),
        api<ChatRecord>(`${API}/documents/${documentId}/chat`),
        api<DocumentPreview>(`${API}/documents/${documentId}/preview`),
        api<MarkdownDocument>(`${API}/documents/${documentId}/markdown`),
      ])
      setInsights(cardData)
      setChat(chatData)
      setDocumentPreview(previewData)
      setMarkdownDocument(markdownData)
      setPreviewTab('original')
      setSearchScope('original')
      setSearchSelection(null)
      setSearchResetKey((key) => key + 1)
      setPreviewPage(1)
      const savedMessages = await api<ChatMessage[]>(`${API}/chats/${chatData.id}/messages`)
      setMessages(savedMessages)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось загрузить результаты документа.')
    }
  }, [showToast])

  useEffect(() => {
    if (!selectedId) {
      setDocument(null)
      setProcessingJob(null)
      setInsights([])
      setDocumentPreview(null)
      setMarkdownDocument(null)
      setChat(null)
      setMessages([])
      return
    }
    let active = true
    let loadedVersion: string | null = null
    setDocument(null)
    setProcessingJob(null)
    setInsights([])
    setDocumentPreview(null)
    setMarkdownDocument(null)
    setPreviewTab('original')
    setSearchScope('original')
    setSearchSelection(null)
    setSearchResetKey((key) => key + 1)
    setChat(null)
    setMessages([])
    setSelectedSourceId(null)
    setSelectedSource(null)
    const refresh = async () => {
      const [documentResult, jobsResult] = await Promise.allSettled([
        api<DocumentRecord>(`${API}/documents/${selectedId}`),
        api<ProcessingJob[]>(`${API}/documents/${selectedId}/jobs`),
      ])
      if (!active) return
      if (documentResult.status === 'fulfilled') {
        const result = documentResult.value
        setDocument(result)
        updateDocumentInLibrary(result)
        // A rebuild can finish between two polls. Reload derived data when
        // its persisted document version changes, even if both polls see ready.
        if (result.status === 'ready' && loadedVersion !== result.updated_at) {
          loadedVersion = result.updated_at
          await loadReadyData(selectedId)
        }
      } else {
        showToast(documentResult.reason instanceof Error ? documentResult.reason.message : 'Не удалось открыть документ.')
      }
      if (jobsResult.status === 'fulfilled') {
        const jobs = jobsResult.value
        setProcessingJob(jobs.find((job) => ['queued', 'running', 'cancelling'].includes(job.state)) ?? jobs[0] ?? null)
      } else {
        // Older API builds may not expose the job endpoint yet. The build
        // version notice handles that deployment mismatch without hiding the document.
        setProcessingJob(null)
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 2_200)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [selectedId, loadReadyData, showToast, updateDocumentInLibrary])

  useEffect(() => {
    if (!pendingMessageNavigation || !selectedId || !messages.length) return
    const target = Array.from(conversation.current?.querySelectorAll<HTMLElement>('[data-message-id]') ?? [])
      .find((element) => element.dataset.messageId === pendingMessageNavigation)
    if (!target) return
    skipNextChatAutoScrollRef.current = true
    scrollIntoViewRespectingMotion(target, 'center')
    setHighlightedMessageId(pendingMessageNavigation)
    setPendingMessageNavigation(null)
    const timer = window.setTimeout(() => setHighlightedMessageId(null), 3_500)
    return () => window.clearTimeout(timer)
  }, [messages, pendingMessageNavigation, selectedId])

  useEffect(() => {
    if (skipNextChatAutoScrollRef.current) {
      skipNextChatAutoScrollRef.current = false
      return
    }
    conversation.current?.scrollTo({ top: conversation.current.scrollHeight, behavior: isSending ? 'auto' : 'smooth' })
  }, [messages, isSending])

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      if (!resizeState.current) return
      const delta = resizeState.current.startX - event.clientX
      setChatWidth(Math.max(320, Math.min(560, resizeState.current.startWidth + delta)))
    }
    const onUp = () => { resizeState.current = null }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])

  useEffect(() => {
    localStorage.setItem('document-checker-chat-width', String(chatWidth))
  }, [chatWidth])

  useEffect(() => {
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(sidebarCollapsed))
  }, [sidebarCollapsed])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (chatActionMenu) {
          event.preventDefault()
          closeChatActionMenu(true)
          return
        }
        if (renameTargetId) {
          event.preventDefault()
          clearChatRename()
          return
        }
        if (openPreferenceMenu) {
          event.preventDefault()
          setOpenPreferenceMenu(null)
          window.requestAnimationFrame(() => {
            const trigger = openPreferenceMenu === 'model' ? modelTriggerRef.current : reasoningTriggerRef.current
            trigger?.focus()
          })
          return
        }
        if (exportOpen) {
          event.preventDefault()
          closeExportDialog()
          return
        }
        setAuthOpen(false)
        setDeleteTarget(null)
        setMobileLibraryOpen(false)
        setMobileChatOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [chatActionMenu, clearChatRename, closeChatActionMenu, closeExportDialog, exportOpen, openPreferenceMenu, renameTargetId])

  useEffect(() => {
    if (!openPreferenceMenu) return
    const onPointerDown = (event: PointerEvent) => {
      if (!authDetailsRef.current?.contains(event.target as Node)) setOpenPreferenceMenu(null)
    }
    window.document.addEventListener('pointerdown', onPointerDown)
    return () => window.document.removeEventListener('pointerdown', onPointerDown)
  }, [openPreferenceMenu])

  useEffect(() => {
    if (!authOpen) setOpenPreferenceMenu(null)
  }, [authOpen])

  useEffect(() => {
    if (!authOpen && !deleteTarget && !exportOpen) return
    const dialog = window.document.querySelector<HTMLElement>(exportOpen ? '.export-dialog' : '.modal-card')
    if (!dialog) return
    const focusableSelector = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])'
    const getFocusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter((element) => element.offsetParent !== null)
    getFocusable()[0]?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      const focusable = getFocusable()
      if (event.key !== 'Tab' || focusable.length < 2) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && window.document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && window.document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    window.document.addEventListener('keydown', onKeyDown)
    return () => window.document.removeEventListener('keydown', onKeyDown)
  }, [authOpen, deleteTarget, exportOpen])

  const uploadFile = useCallback(async (file?: File) => {
    if (!file) return
    const extension = `.${file.name.split('.').pop()?.toLowerCase() ?? ''}`
    if (!['.pdf', '.docx', '.txt', '.md', '.csv', '.xml', '.xlsx', '.xls', '.pptx', '.html', '.htm', '.json', '.epub'].includes(extension)) {
      showToast('Поддерживаются PDF, DOCX, TXT, MD, CSV, XML, XLSX, XLS, PPTX, HTML, JSON и EPUB.')
      return
    }
    if (file.size > 25 * 1024 * 1024) {
      showToast('Файл превышает максимальный размер 25 МБ.')
      return
    }
    setIsUploading(true)
    setMobileLibraryOpen(false)
    const body = new FormData()
    body.append('file', file)
    try {
      const created = await api<DocumentRecord>(`${API}/documents`, { method: 'POST', body })
      setNewChatOpen(false)
      setSelectedId(created.id)
      void refreshLibrary(created.id)
      setPreviewOpen(true)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось загрузить документ.')
    } finally {
      setIsUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }, [refreshLibrary, showToast])

  const startNewChat = useCallback(() => {
    setNewChatOpen(true)
    setSelectedId(null)
    setDocument(null)
    setInsights([])
    setDocumentPreview(null)
    setMarkdownDocument(null)
    setPreviewTab('original')
    setPreviewPage(1)
    setChat(null)
    setMessages([])
    setChatInput('')
    setStreamText('')
    setStreamSources([])
    setSelectedSourceId(null)
    setSelectedSource(null)
    setSearchSelection(null)
    setSearchScope('original')
    setSearchResetKey((key) => key + 1)
    setChatOpen(true)
    setChatFull(false)
    setMobileLibraryOpen(false)
    setMobileChatOpen(false)
  }, [])

  const openSource = useCallback(async (source: SourceRef | StreamCitation) => {
    if (!document) return
    searchNavigationRef.current += 1
    setSearchResetKey((key) => key + 1)
    setPreviewOpen(true)
    setPreviewTab('original')
    setSearchScope('original')
    setSearchSelection(null)
    setSelectedSourceId(source.id)
    setSelectedSource(source)
    const page = source.locator.page
    if (typeof page === 'number' && page > 0) setPreviewPage(page)
    window.requestAnimationFrame(() => {
      scrollIntoViewRespectingMotion(window.document.getElementById('document-original-viewer'))
    })
    setMobileChatOpen(false)
  }, [document])

  const clearDocumentSearch = useCallback(() => {
    searchNavigationRef.current += 1
    setSearchSelection(null)
    setSelectedSourceId(null)
    setSelectedSource(null)
  }, [])

  const changeDocumentSearchScope = useCallback((scope: DocumentSearchScope) => {
    setSearchScope(scope)
    setPreviewTab(scope)
    clearDocumentSearch()
  }, [clearDocumentSearch])

  const navigateDocumentSearch = useCallback(async (match: DocumentSearchMatch, scope: DocumentSearchScope) => {
    const currentDocument = documentRef.current
    if (!currentDocument || selectedIdRef.current !== currentDocument.id) return
    const navigation = ++searchNavigationRef.current
    const source: SourceRef = {
      id: match.id,
      text: match.text,
      locator: match.locator,
      ordinal: match.ordinal,
      is_derived: false,
    }
    setPreviewOpen(true)
    setSearchScope(scope)
    setPreviewTab(scope)
    setSelectedSourceId(match.id)
    setSelectedSource(source)
    setSearchSelection(scope === 'markdown' ? match : null)
    const page = match.locator.page
    if (scope === 'original' && typeof page === 'number' && page > 0) setPreviewPage(page)

    if (scope === 'markdown' && match.markdown_start !== null && match.markdown_end !== null) {
      const currentMarkdown = markdownDocumentRef.current
      const loadedStart = currentMarkdown?.offset ?? 0
      const loadedEnd = loadedStart + (currentMarkdown ? Array.from(currentMarkdown.markdown).length : 0)
      if (!currentMarkdown || match.markdown_start < loadedStart || match.markdown_end > loadedEnd) {
        const offset = Math.max(0, match.markdown_start - 25_000)
        const segment = await api<MarkdownDocument>(API + '/documents/' + currentDocument.id + '/markdown?offset=' + offset + '&limit=100000')
        if (navigation !== searchNavigationRef.current || selectedIdRef.current !== currentDocument.id) return
        setMarkdownDocument(segment)
      }
    }
    window.requestAnimationFrame(() => {
      if (navigation !== searchNavigationRef.current || selectedIdRef.current !== currentDocument.id) return
      scrollIntoViewRespectingMotion(window.document.getElementById('document-original-viewer'))
      if (scope === 'markdown') {
        window.requestAnimationFrame(() => {
          if (navigation === searchNavigationRef.current) {
            scrollIntoViewRespectingMotion(window.document.querySelector('[data-search-match="true"]'))
          }
        })
      }
    })
  }, [])

  const beginLogin = useCallback(async () => {
    try {
      const response = await api<{ verification_url?: string; user_code?: string; already_authenticated?: boolean }>(`${API}/codex/login/device-code`, { method: 'POST' })
      if (response.already_authenticated) {
        showToast('Аккаунт Codex уже подключён.')
        return
      }
      setCodex((current) => current ? { ...current, login_state: 'pending', verification_url: response.verification_url ?? null, user_code: response.user_code ?? null } : current)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось начать вход в Codex.')
    }
  }, [showToast])

  const saveCodexPreferences = useCallback(async (model: string, reasoningEffort: string): Promise<boolean> => {
    setCodexSaving(true)
    setCodexPreferenceMessage('')
    try {
      const updated = await api<CodexStatus>(`${API}/codex/preferences`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, reasoning_effort: reasoningEffort }),
      })
      setCodex(updated)
      setCodexPreferenceMessage('Настройки модели сохранены.')
      return true
    } catch (error) {
      setCodexPreferenceMessage(error instanceof Error ? error.message : 'Не удалось сохранить настройки модели.')
      return false
    } finally {
      setCodexSaving(false)
    }
  }, [])

  const changeCodexModel = useCallback((model: string) => {
    const option = codex?.models.find((item) => item.id === model)
    const currentEffort = codex?.reasoning_effort || DEFAULT_CODEX_REASONING
    const nextEffort = option?.reasoning_efforts.some((item) => item.value === currentEffort)
      ? currentEffort
      : option?.reasoning_efforts[0]?.value || currentEffort
    return saveCodexPreferences(model, nextEffort)
  }, [codex, saveCodexPreferences])

  const changeCodexReasoning = useCallback((reasoningEffort: string) => {
    return saveCodexPreferences(codex?.model || DEFAULT_CODEX_MODEL, reasoningEffort)
  }, [codex, saveCodexPreferences])

  const retryDocument = useCallback(async (operation: 'retry' | 'process' = 'retry') => {
    if (!document || processingActionPending) return
    setProcessingActionPending(true)
    try {
      const updated = await api<DocumentRecord>(`${API}/documents/${document.id}/retry${operation === 'process' ? '?operation=process' : ''}`, { method: 'POST' })
      setDocument(updated)
      if (operation === 'process') {
        const jobs = await api<ProcessingJob[]>(`${API}/documents/${document.id}/jobs`)
        setProcessingJob(jobs.find((job) => ['queued', 'running', 'cancelling'].includes(job.state)) ?? jobs[0] ?? null)
      } else {
        setProcessingJob(null)
      }
      updateDocumentInLibrary(updated)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось повторить обработку.')
    } finally {
      setProcessingActionPending(false)
    }
  }, [document, processingActionPending, showToast, updateDocumentInLibrary])

  const reprocessDocumentOCR = useCallback(async (options: OcrReprocessOptions): Promise<boolean> => {
    if (!document || processingActionPending) return false
    setProcessingActionPending(true)
    try {
      const updated = await api<DocumentRecord>(`${API}/documents/${document.id}/ocr/reprocess`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(options),
      })
      const jobs = await api<ProcessingJob[]>(`${API}/documents/${document.id}/jobs`)
      setDocument(updated)
      setProcessingJob(jobs.find((job) => ['queued', 'running', 'cancelling'].includes(job.state)) ?? jobs[0] ?? null)
      updateDocumentInLibrary(updated)
      showToast('Повторный OCR поставлен в очередь. Предыдущая версия доступна до завершения обработки.')
      return true
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось запустить повторный OCR.')
      return false
    } finally {
      setProcessingActionPending(false)
    }
  }, [document, processingActionPending, showToast, updateDocumentInLibrary])

  const cancelProcessing = useCallback(async () => {
    if (!selectedId || processingActionPending) return
    setProcessingActionPending(true)
    try {
      await api<{ status: string }>(`${API}/documents/${selectedId}/cancel`, { method: 'POST' })
      const [updatedDocument, jobs] = await Promise.all([
        api<DocumentRecord>(`${API}/documents/${selectedId}`),
        api<ProcessingJob[]>(`${API}/documents/${selectedId}/jobs`),
      ])
      setDocument(updatedDocument)
      updateDocumentInLibrary(updatedDocument)
      setProcessingJob(jobs.find((job) => ['queued', 'running', 'cancelling'].includes(job.state)) ?? jobs[0] ?? null)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось отменить обработку.')
    } finally {
      setProcessingActionPending(false)
    }
  }, [selectedId, processingActionPending, showToast, updateDocumentInLibrary])

  const rebuildMarkdown = useCallback(async () => {
    if (!document || markdownRebuilding) return
    setMarkdownRebuilding(true)
    try {
      const updated = await api<DocumentRecord>(`${API}/documents/${document.id}/markdown/rebuild`, { method: 'POST' })
      setDocument(updated)
      setMarkdownDocument({
        document_id: updated.id,
        status: updated.markdown_status,
        source: updated.analysis_source,
        converter_version: updated.markdown_converter_version,
        markdown: '',
        offset: 0,
        limit: 0,
        line_offset: 1,
        total_chars: updated.markdown_char_count,
        total_lines: updated.markdown_line_count,
        checksum: updated.markdown_checksum,
        mapping_quality: updated.markdown_mapping,
        error: updated.markdown_error,
      })
      updateDocumentInLibrary(updated)
      showToast('Markdown поставлен в очередь на создание.')
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось создать Markdown.')
    } finally {
      setMarkdownRebuilding(false)
    }
  }, [document, markdownRebuilding, showToast, updateDocumentInLibrary])

  const loadMoreMarkdown = useCallback(async () => {
    if (!document || !markdownDocument || markdownLoadingMore || markdownDocument.status !== 'ready') return
    const offset = markdownDocument.offset + Array.from(markdownDocument.markdown).length
    if (offset >= markdownDocument.total_chars) return
    setMarkdownLoadingMore(true)
    try {
      const next = await api<MarkdownDocument>(`${API}/documents/${document.id}/markdown?offset=${offset}&limit=250000`)
      setMarkdownDocument((current) => current ? {
        ...next,
        markdown: current.markdown + next.markdown,
        offset: 0,
        limit: current.limit + next.limit,
        line_offset: current.line_offset,
      } : next)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось загрузить продолжение Markdown.')
    } finally {
      setMarkdownLoadingMore(false)
    }
  }, [document, markdownDocument, markdownLoadingMore, showToast])

  const confirmDelete = useCallback(async () => {
    if (!deleteTarget) return
    try {
      await api<void>(`${API}/documents/${deleteTarget.id}`, { method: 'DELETE' })
      setChats((current) => current.filter((item) => item.document_id !== deleteTarget.id))
      setChatLibraryTotal((current) => Math.max(0, current - 1))
      if (selectedId === deleteTarget.id) setSelectedId(null)
      setDeleteTarget(null)
      void refreshLibrary()
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось удалить документ.')
    }
  }, [deleteTarget, refreshLibrary, selectedId, showToast])

  const sendMessage = useCallback(async () => {
    const text = chatInput.trim()
    if (!text || !chat || isSending) return
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(), role: 'user', content: text, citations: [], created_at: new Date().toISOString(),
    }
    setMessages((current) => [...current, userMessage])
    setChatInput('')
    setIsSending(true)
    setStreamText('')
    setStreamSources([])
    let streamed = ''
    let receivedFinal = false
    try {
      const response = await fetch(`${API}/chats/${chat.id}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      if (!response.ok) {
        let message = `Ошибка запроса (${response.status})`
        try {
          const body = await response.json() as { detail?: string }
          if (body.detail) message = body.detail
        } catch {
          // Use the status message if the API returned a proxy error.
        }
        throw new Error(message)
      }
      if (!response.body) throw new Error('Поток ответа недоступен.')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true }).replace(/\r/g, '')
        let boundary = buffer.indexOf('\n\n')
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const event = block.split('\n').find((line) => line.startsWith('event:'))?.slice(6).trim()
          const dataLine = block.split('\n').find((line) => line.startsWith('data:'))?.slice(5).trim()
          if (event && dataLine) {
            const data = JSON.parse(dataLine) as { text?: string; answer?: string; citations?: StreamCitation[]; sources?: StreamCitation[]; message?: string }
            if (event === 'sources') setStreamSources(data.sources ?? [])
            if (event === 'delta' && data.text) {
              streamed += data.text
              setStreamText((current) => current + data.text)
            }
            if (event === 'done') {
              receivedFinal = true
              const finalText = data.answer ?? streamed
              const citations = (data.citations ?? []).map((item) => ({ ...item, locator: item.locator }))
              setMessages((current) => [...current, {
                id: crypto.randomUUID(), role: 'assistant', content: finalText, citations,
                created_at: new Date().toISOString(),
              }])
              void refreshLibrary()
              setStreamText('')
              setStreamSources([])
            }
            if (event === 'error') throw new Error(data.message ?? 'Не удалось получить ответ.')
          }
          boundary = buffer.indexOf('\n\n')
        }
      }
      if (!receivedFinal) throw new Error('Поток ответа завершился до получения итогового ответа. Повторите вопрос.')
    } catch (error) {
      setStreamText('')
      setStreamSources([])
      showToast(error instanceof Error ? error.message : 'Не удалось получить ответ от Codex.')
    } finally {
      setIsSending(false)
    }
  }, [chat, chatInput, isSending, refreshLibrary, showToast])

  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    resizeState.current = { startX: event.clientX, startWidth: chatWidth }
  }

  const resizeByKeyboard = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowLeft') setChatWidth((width) => Math.min(560, width + 16))
    if (event.key === 'ArrowRight') setChatWidth((width) => Math.max(320, width - 16))
  }

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setUploadActive(false)
    void uploadFile(event.dataTransfer.files[0])
  }

  const authReady = Boolean(codex?.authenticated && codex.model_available && codex.reasoning_available)
  const catalogModelOptions = (codex?.models ?? []).filter((item) => ALLOWED_CODEX_MODELS.has(item.id.toLowerCase()))
  const unavailableCurrentModel = codex?.model && ALLOWED_CODEX_MODELS.has(codex.model.toLowerCase()) && catalogModelOptions.length && !catalogModelOptions.some((item) => item.id === codex.model) ? [{
    id: codex.model,
    label: `${codexModelLabel(codex)} · недоступна`,
    description: 'Эта модель больше не доступна для текущего аккаунта.',
    reasoning_efforts: [],
  }] : []
  const codexModelOptions = catalogModelOptions.length ? [...unavailableCurrentModel, ...catalogModelOptions] : [{
    id: codex?.model || DEFAULT_CODEX_MODEL,
    label: codexModelLabel(codex),
    description: '',
    reasoning_efforts: [{
      value: codex?.reasoning_effort || DEFAULT_CODEX_REASONING,
      label: codexReasoningLabel(codex?.reasoning_effort),
      description: '',
    }],
  }]
  const selectedCodexModel = codexModelOptions.find((item) => item.id === (codex?.model || DEFAULT_CODEX_MODEL)) || codexModelOptions[0]
  const codexReasoningOptions = selectedCodexModel?.reasoning_efforts ?? []
  const modelMenuDisabled = !codex?.authenticated || !catalogModelOptions.length || codexSaving
  const reasoningMenuDisabled = !codex?.authenticated || !codex?.model_available || !codexReasoningOptions.length || codexSaving
  const togglePreferenceMenu = (menu: 'model' | 'reasoning') => {
    if ((menu === 'model' && modelMenuDisabled) || (menu === 'reasoning' && reasoningMenuDisabled)) return
    if (openPreferenceMenu === menu) {
      setOpenPreferenceMenu(null)
      return
    }
    setOpenPreferenceMenu(menu)
    window.requestAnimationFrame(() => {
      const list = window.document.getElementById(`codex-${menu}-options`)
      const selected = list?.querySelector<HTMLElement>('[aria-selected="true"]')
      const first = list?.querySelector<HTMLElement>('[role="option"]')
      ;(selected || first)?.focus()
    })
  }
  const movePreferenceFocus = (menu: 'model' | 'reasoning', event: React.KeyboardEvent<HTMLButtonElement>) => {
    const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End']
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      setOpenPreferenceMenu(null)
      window.requestAnimationFrame(() => (menu === 'model' ? modelTriggerRef.current : reasoningTriggerRef.current)?.focus())
      return
    }
    if (!keys.includes(event.key)) return
    event.preventDefault()
    const options = Array.from(window.document.querySelectorAll<HTMLElement>(`#codex-${menu}-options [role="option"]`)).filter((option) => !option.hasAttribute('disabled'))
    if (!options.length) return
    const currentIndex = options.indexOf(event.currentTarget)
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (currentIndex + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length
    options[nextIndex]?.focus()
  }
  const selectCodexModel = async (model: string) => {
    setOpenPreferenceMenu(null)
    const saved = await changeCodexModel(model)
    if (!saved) setOpenPreferenceMenu('model')
  }
  const selectCodexReasoning = async (reasoningEffort: string) => {
    setOpenPreferenceMenu(null)
    const saved = await changeCodexReasoning(reasoningEffort)
    if (!saved) setOpenPreferenceMenu('reasoning')
  }
  const streamedLabels = Array.from(new Set(Array.from(streamText.matchAll(/\[(S\d{2})\]/g), (match) => match[1])))
  const streamingCitations = streamedLabels.flatMap((label) => {
    const source = streamSources.find((item) => item.label === label)
    return source ? [source] : []
  })
  const streamingContent = streamText.replace(/\[(S\d{2})\]/g, (_marker, label: string) => {
    const position = streamingCitations.findIndex((source) => source.label === label)
    return position >= 0 ? `〔${position + 1}〕` : ''
  })
  const currentListItem = chats.find((item) => item.document_id === selectedId)
  const visibleStatus = document ?? (currentListItem ? summaryToDocument(currentListItem) : null)
  const activeJob = Boolean(processingJob && ['queued', 'running', 'cancelling'].includes(processingJob.state))
  const activeStatus = visibleStatus ? ['queued', 'extracting', 'ocr', 'indexing', 'analyzing'].includes(visibleStatus.status) || (visibleStatus.status === 'ready' && activeJob) : false
  const replacementFailure = visibleStatus?.status === 'ready' && processingJob?.state === 'failed'
  const mainClasses = [
    'app-shell',
    sidebarCollapsed ? 'library-manual-collapsed' : '',
    !selectedId ? 'empty-state' : '',
    !chatOpen ? 'chat-hidden' : '',
    chatFull ? 'chat-full' : '',
    mobileLibraryOpen ? 'mobile-library-open' : '',
    mobileChatOpen ? 'mobile-chat-open' : '',
  ].filter(Boolean).join(' ')
  const pinnedChats = chats.filter((item) => item.pinned)
  const recentChats = chats.filter((item) => !item.pinned)
  const activeMenuItem = chatActionMenu ? chats.find((item) => item.id === chatActionMenu.chatId) : null

  const renderChatRow = (item: ChatSummary) => {
    const isRenaming = renameTargetId === item.id
    return (
      <div key={item.id} className={`document-row ${selectedId === item.document_id ? 'selected' : ''} ${item.pinned ? 'is-pinned' : ''}`} data-chat-id={item.id}>
        {isRenaming ? (
          <form className="chat-rename-form" onSubmit={(event) => { event.preventDefault(); void saveChatRename(item) }}>
            <input
              autoFocus
              aria-label="Название чата"
              aria-invalid={Boolean(renameError)}
              maxLength={72}
              value={renameDraft}
              onChange={(event) => { setRenameDraft(event.target.value); setRenameError('') }}
              onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); clearChatRename() } }}
            />
            <button className="icon-button" type="submit" aria-label="Сохранить название чата" title="Сохранить" disabled={chatSettingsPendingId === item.id}><Check size={15} /></button>
            <button className="icon-button" type="button" aria-label="Отменить переименование" title="Отмена" onClick={clearChatRename} disabled={chatSettingsPendingId === item.id}><X size={15} /></button>
            {renameError && <span className="chat-rename-error" role="alert">{renameError}</span>}
          </form>
        ) : (
          <>
            <button className="document-select" onClick={() => selectLibraryChat(item)} title={item.filename} aria-label={`Открыть чат ${item.title}`}>
              <span className="document-type-icon">{fileIcon(item.file_type, 17)}</span>
              <span className="document-row-text">
                <span className="document-row-name">{item.title}</span>
                <span className="document-row-meta">
                  <span className={`status-dot status-${item.status}`} />
                  {item.message_count ? `${item.message_count} ${pluralLabel(item.message_count, 'сообщение', 'сообщения', 'сообщений')}` : statusLabel(item.status)}
                  <span className="row-meta-divider">·</span>{relativeDate(item.last_activity_at)}
                  {item.pinned && <span className="chat-pinned-indicator"><Pin size={11} /> Закреплён</span>}
                </span>
                {debouncedChatSearch && <span className="chat-search-snippet">{item.search_snippet ?? item.last_message_preview ?? item.filename}</span>}
              </span>
            </button>
            <button
              className="row-actions-trigger icon-button"
              type="button"
              aria-label={`Действия: ${item.title}`}
              aria-haspopup="menu"
              aria-expanded={chatActionMenu?.chatId === item.id}
              aria-controls="chat-actions-menu"
              title={`Действия: ${item.title}`}
              data-chat-actions-trigger={item.id}
              onClick={(event) => openChatActionMenu(event, item)}
            >
              {chatSettingsPendingId === item.id ? <LoaderCircle className="spin" size={15} /> : <MoreHorizontal size={16} />}
            </button>
          </>
        )}
      </div>
    )
  }

  return (
    <div className={mainClasses} style={{ '--chat-width': `${chatWidth}px` } as React.CSSProperties}>
      <a className="skip-link" href="#main-content">К содержанию</a>
      <header className="topbar">
        <div className="topbar-brand">
          <button className="icon-button mobile-menu" aria-label="Открыть документы" onClick={() => setMobileLibraryOpen(true)}><Menu size={19} /></button>
          <div className="brand-mark" aria-hidden="true"><span /><span /><span /><span /></div>
          <span className="brand-name">document<span>checker</span></span>
          <span className="brand-divider" />
          <span className="brand-context">Рабочее пространство</span>
        </div>
        <div className="topbar-actions">
          <button
            className={`connection-button ${authReady ? 'is-connected' : ''}`}
            aria-label={authReady ? `Codex подключён: ${codexModelLabel(codex)}, ${codexReasoningLabel(codex?.reasoning_effort)}` : 'Подключить Codex'}
            onClick={() => setAuthOpen(true)}
          >
            {authReady ? <ShieldCheck size={15} /> : <CircleHelp size={15} />}
            <span>{authReady ? 'Codex подключён' : 'Подключить Codex'}</span>
            {authReady && <span className="connection-model">{codexModelLabel(codex)} · {codexReasoningLabel(codex?.reasoning_effort)}</span>}
          </button>
          <button className="icon-button chat-visibility-toggle" type="button" aria-label={chatVisible ? 'Свернуть чат' : 'Открыть чат'} title={chatVisible ? 'Свернуть чат' : 'Открыть чат'} aria-expanded={chatVisible} aria-controls="document-chat" onClick={toggleChat}>{chatVisible ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}</button>
          <button className="button button-dark header-upload" onClick={() => fileInput.current?.click()} disabled={isUploading}>
            {isUploading ? <LoaderCircle className="spin" size={16} /> : <FileUp size={16} />}
            <span>Загрузить файл</span>
          </button>
        </div>
      </header>

      <aside id="chat-library" className={`library ${sidebarCollapsed ? 'library-manual-collapsed' : ''}`} aria-label="История чатов" aria-expanded={!sidebarCollapsed}>
        <div className="library-heading">
          <div className="library-title">Чаты</div>
          <button className="icon-button collapse-library" aria-label={sidebarCollapsed ? 'Развернуть библиотеку' : 'Свернуть библиотеку'} aria-controls="chat-library" aria-expanded={!sidebarCollapsed} title={sidebarCollapsed ? 'Развернуть библиотеку' : 'Свернуть библиотеку'} onClick={() => setSidebarCollapsed((value) => !value)}>
            {sidebarCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
          <button className="icon-button close-mobile-panel" aria-label="Закрыть библиотеку" onClick={() => setMobileLibraryOpen(false)}><X size={18} /></button>
        </div>
        <button className="library-add" title="Новый чат" aria-label="Новый чат" onClick={startNewChat} disabled={isUploading}>
          {isUploading ? <LoaderCircle className="spin" size={17} /> : <FileUp size={17} />}
          <span>Новый чат</span>
        </button>
        <div className="library-search-wrap">
          <Search className="library-search-icon" size={15} aria-hidden="true" />
          <input
            ref={librarySearchRef}
            type="search"
            aria-label="Поиск по чатам"
            placeholder="Название, файл или сообщение"
            maxLength={120}
            value={chatSearch}
            onChange={(event) => setChatSearch(event.target.value)}
          />
          {chatSearch && <button className="library-search-clear" type="button" aria-label="Очистить поиск чатов" title="Очистить поиск" onClick={() => setChatSearch('')}><X size={14} /></button>}
        </div>
        <button className="library-search-collapsed-trigger icon-button" type="button" aria-label="Поиск по чатам" title="Поиск по чатам" onClick={focusLibrarySearch}><Search size={17} /></button>
        <div className="library-list-heading library-results-heading" aria-live="polite">
          <span>{debouncedChatSearch ? 'Результаты поиска' : 'Недавние чаты'}</span>
          <span className="count-badge">{chatLibraryTotal}</span>
        </div>
        <div className="document-list">
          {pinnedChats.length > 0 && <>
            <div className="library-list-heading library-group-heading"><span>Закреплённые</span><span className="count-badge">{pinnedChats.length}</span></div>
            {pinnedChats.map(renderChatRow)}
          </>}
          {recentChats.length > 0 && <>
            {pinnedChats.length > 0 && <div className="library-list-heading library-group-heading">Недавние</div>}
            {recentChats.map(renderChatRow)}
          </>}
          {chats.length === 0 && chatLibraryLoading && <div className="library-loading" aria-label="Загружаю библиотеку чатов"><span /><span /><span /></div>}
          {chats.length === 0 && !chatLibraryLoading && !chatLibraryError && <div className="library-empty">{debouncedChatSearch ? <>Ничего не найдено по запросу «{debouncedChatSearch}».<button type="button" onClick={() => setChatSearch('')}>Очистить поиск</button></> : 'Чаты с документами появятся здесь'}</div>}
          {chatLibraryError && <div className="library-error" role="alert"><span>{chatLibraryError}</span><button type="button" onClick={() => void refreshLibrary()}>Повторить</button></div>}
          {chatLibraryHasMore && <button className="library-load-more" type="button" onClick={() => void loadMoreChats()} disabled={chatLibraryLoadingMore || chatLibraryLoading}>
            {chatLibraryLoadingMore ? <><LoaderCircle className="spin" size={14} /> Загружаю…</> : `Показать ещё чаты (${Math.max(0, chatLibraryTotal - chats.length)})`}
          </button>}
        </div>
        <div className="library-footer">
          <span className="local-lock"><ShieldCheck size={14} /> История и документы хранятся локально</span>
          <span>до 25 МБ на файл</span>
        </div>
      </aside>

      {chatActionMenu && activeMenuItem && createPortal(
        <div
          id="chat-actions-menu"
          className="chat-actions-menu"
          role="menu"
          aria-label={`Действия с чатом ${activeMenuItem.title}`}
          ref={chatMenuRef}
          style={{ top: chatActionMenu.top, left: chatActionMenu.left }}
          onKeyDown={handleChatMenuKeyDown}
        >
          <button type="button" role="menuitem" tabIndex={-1} onClick={() => beginChatRename(activeMenuItem)}><Pencil size={15} /> Переименовать</button>
          <button type="button" role="menuitem" tabIndex={-1} onClick={() => void toggleChatPin(activeMenuItem)}>{activeMenuItem.pinned ? <PinOff size={15} /> : <Pin size={15} />} {activeMenuItem.pinned ? 'Открепить чат' : 'Закрепить чат'}</button>
          {activeMenuItem.custom_title && <button type="button" role="menuitem" tabIndex={-1} onClick={() => void resetChatTitle(activeMenuItem)}><RotateCw size={15} /> Вернуть исходное название</button>}
          <div className="chat-actions-divider" />
          <button type="button" role="menuitem" tabIndex={-1} className="chat-actions-delete" onClick={() => { closeChatActionMenu(); setDeleteTarget(summaryToDocument(activeMenuItem)) }}><Trash2 size={15} /> Удалить документ и чат</button>
        </div>,
        window.document.body,
      )}

      <main id="main-content" tabIndex={-1} className={`workspace ${!selectedId || !visibleStatus ? 'workspace-empty' : ''} ${uploadActive ? 'drop-active' : ''}`} ref={workArea} onDragOver={(event) => { event.preventDefault(); setUploadActive(true) }} onDragLeave={(event) => { if (event.currentTarget === event.target) setUploadActive(false) }} onDrop={handleDrop}>
        <BuildVersionNotice check={buildVersionCheck} busy={isUploading || isSending} onReload={() => window.location.reload()} onRetry={recheckBuildVersion} />
        <input ref={fileInput} className="visually-hidden" type="file" name="document" accept={ACCEPTED} aria-label="Выберите документ" onChange={(event) => void uploadFile(event.target.files?.[0])} />
        {uploadActive && <div className="drop-overlay"><FileUp size={24} /><strong>Отпустите файл, чтобы загрузить</strong><span>PDF, DOCX, TXT, MD, CSV, XML, XLSX, XLS, PPTX, HTML, JSON или EPUB</span></div>}

        {!selectedId || !visibleStatus ? (
          <EmptyWorkspace
            isUploading={isUploading}
            onChoose={() => fileInput.current?.click()}
            documentsCount={chats.length}
            onToggleLibrary={() => setSidebarCollapsed((value) => !value)}
            onExpandChat={() => setChatFull(true)}
          />
        ) : (
          <div className="document-workspace">
            <div className="document-toolbar">
              <div className="document-title-wrap">
                <div className="file-badge">{fileIcon(visibleStatus.file_type, 20)}</div>
                <div className="document-title-text">
                  <h1 title={visibleStatus.filename}>{visibleStatus.filename}</h1>
                  <span>{visibleStatus.file_type.toUpperCase()} <span className="meta-divider">·</span> {formatBytes(visibleStatus.file_size)}</span>
                </div>
              </div>
              <div className="document-toolbar-actions">
                {visibleStatus.status === 'ready' && document && <button ref={exportTriggerRef} className="button button-light export-trigger" type="button" onClick={startExport} disabled={isSending || exportPending} title={isSending ? 'Дождитесь завершения ответа' : 'Скачать сохранённые ответы и источники'}><Download size={15} /> Экспорт</button>}
                {visibleStatus.status === 'error' && <button className="icon-button" title="Повторить обработку" aria-label="Повторить обработку" onClick={() => void retryDocument()}><RotateCw size={16} /></button>}
              </div>
            </div>

            {(activeStatus || replacementFailure) && <ProcessingStatusPanel
              job={processingJob}
              status={visibleStatus.status}
              fallbackDescription={processingDescription(visibleStatus.status)}
              replacementFailure={replacementFailure}
              actionPending={processingActionPending}
              onCancel={processingJob && ['queued', 'running'].includes(processingJob.state) ? () => void cancelProcessing() : undefined}
              onRetry={replacementFailure ? () => void retryDocument() : undefined}
            />}

            {document && (document.ocr_status === 'partial' || (document.ocr_status === 'ready' && document.analysis_source === 'ocr')) && (
              <div className="ocr-notice" role="status"><BookOpen size={16} /><span>{document.ocr_status === 'partial' ? `${document.ocr_error || 'Не удалось распознать часть страниц.'} Доступный текст сохранён, оригинал документа доступен без изменений.` : `Текст сканированных страниц распознан локально (${document.ocr_language || 'rus+eng'}), а оригинал сохранён без изменений.`}</span></div>
            )}

            {(visibleStatus.status === 'needs_auth' || visibleStatus.status === 'model_unavailable' || visibleStatus.status === 'error' || visibleStatus.status === 'cancelled') && (
              <div className={`issue-banner ${visibleStatus.status === 'error' ? 'issue-error' : ''}`} role="alert">
                <CircleHelp size={19} />
                <div className="issue-copy"><strong>{visibleStatus.status === 'needs_auth' ? 'Подключите Codex, чтобы получить ответы' : visibleStatus.status === 'model_unavailable' ? 'Выбранная модель недоступна' : visibleStatus.status === 'cancelled' ? 'Обработка отменена' : visibleStatus.ocr_status === 'failed' ? 'Не удалось распознать скан' : 'Не удалось обработать документ'}</strong><span>{visibleStatus.error_message ?? visibleStatus.ocr_error ?? (visibleStatus.status === 'cancelled' ? 'Можно запустить обработку повторно.' : 'Проверьте настройки и повторите действие.')}</span></div>
                {visibleStatus.status === 'needs_auth' ? <button className="button button-dark" onClick={() => setAuthOpen(true)}>Подключить</button> : <button className="button button-light" onClick={() => void retryDocument()} disabled={processingActionPending}><RotateCw size={15} /> Повторить</button>}
              </div>
            )}

            {visibleStatus.status === 'ready' && document && (
              <div className="workspace-scroll" key={document.id}>
                <div className="document-facts">
                  <div className="fact-item"><BookOpen size={15} /><span><b>{document.chunk_count}</b> {pluralLabel(document.chunk_count, 'фрагмент текста', 'фрагмента текста', 'фрагментов текста')}</span></div>
                  {typeof document.metadata.page_count === 'number' && <div className="fact-item"><AlignLeft size={15} /><span><b>{document.metadata.page_count}</b> стр.</span></div>}
                  {typeof document.metadata.row_count === 'number' && <div className="fact-item"><FileSpreadsheet size={15} /><span><b>{document.metadata.row_count}</b> строк · <b>{String(document.metadata.column_count ?? 0)}</b> столбцов</span></div>}
                  <div className="fact-item"><Clock3 size={15} /><span>Добавлен {relativeDate(document.created_at)}</span></div>
                </div>

                <div className={`document-analysis-layout ${['csv', 'xlsx', 'xls'].includes(documentPreview?.renderer || '') ? 'is-table-layout' : ''}`}>
                <section id="document-original-viewer" className={`source-viewer ${previewOpen ? 'viewer-open' : 'viewer-closed'}`} aria-label="Оригинал документа">
                  <div className="viewer-heading">
                    <div className="viewer-heading-label"><BookOpen size={16} /><strong>Оригинал документа</strong><span>{documentPreview ? `${documentPreview.source_count} ${pluralLabel(documentPreview.source_count, 'источник', 'источника', 'источников')}` : `${document.chunk_count} ${pluralLabel(document.chunk_count, 'источник', 'источника', 'источников')}`}</span></div>
                    <div className="viewer-controls">
                      {documentPreview?.renderer === 'pdf' && <>
                        <span className="preview-page-label">{previewPage} / {documentPreview.page_count ?? '—'}</span>
                        <button className="icon-button" disabled={previewPage <= 1} aria-label="Предыдущая страница" onClick={() => setPreviewPage((value) => Math.max(1, value - 1))}><ChevronLeft size={17} /></button>
                        <button className="icon-button" disabled={previewPage >= (documentPreview.page_count ?? 1)} aria-label="Следующая страница" onClick={() => setPreviewPage((value) => Math.min(documentPreview.page_count ?? value + 1, value + 1))}><ChevronRight size={17} /></button>
                      </>}
                      <button className="icon-button viewer-toggle" aria-expanded={previewOpen} aria-label={previewOpen ? 'Свернуть просмотр документа' : 'Развернуть просмотр документа'} onClick={() => setPreviewOpen((value) => !value)}>{previewOpen ? <ChevronDown size={17} /> : <ChevronRight size={17} />}</button>
                    </div>
                  </div>
                  {previewOpen && (
                    <div className="document-preview" aria-busy={!documentPreview}>
                      {!documentPreview ? (
                        <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Загружаю предпросмотр…</div>
                      ) : (
                        <>
                          <div className="preview-tabs" role="tablist" aria-label="Представление документа">
                            <button type="button" role="tab" aria-selected={previewTab === 'original'} className={`preview-tab ${previewTab === 'original' ? 'is-active' : ''}`} onClick={() => { setSearchScope('original'); setPreviewTab('original') }}>Оригинал</button>
                            <button type="button" role="tab" aria-selected={previewTab === 'markdown'} className={`preview-tab ${previewTab === 'markdown' ? 'is-active' : ''}`} onClick={() => { setSearchScope('markdown'); setPreviewTab('markdown') }}>Markdown</button>
                            {markdownDocument?.status === 'ready' && <a className="preview-download" href={`${API}/documents/${document.id}/markdown/download`} download>Скачать .md</a>}
                          </div>
                          <DocumentSearchToolbar
                            documentId={document.id}
                            resetKey={searchResetKey}
                            scope={searchScope}
                            onScopeChange={changeDocumentSearchScope}
                            onNavigate={navigateDocumentSearch}
                            onClear={clearDocumentSearch}
                          />
                          {previewTab === 'original' ? (
                            documentPreview.original_url ? <OriginalDocumentViewer document={document} preview={documentPreview} selectedSource={selectedSource} selectedSourceId={selectedSourceId} originalUrl={documentPreview.original_url} pageNumber={previewPage} onReprocess={reprocessDocumentOCR} reprocessing={processingActionPending || Boolean(processingJob && ['queued', 'running', 'cancelling'].includes(processingJob.state))} /> : <div className="preview-render-error" role="alert"><TriangleAlert size={18} /><span>Оригинальный файл недоступен.</span></div>
                          ) : markdownDocument ? (
                            <MarkdownViewer data={markdownDocument} selectedSource={selectedSource} searchMatch={searchSelection && searchSelection.markdown_start !== null ? searchSelection : null} onRebuild={() => void rebuildMarkdown()} rebuilding={markdownRebuilding} onLoadMore={() => void loadMoreMarkdown()} loadingMore={markdownLoadingMore} />
                          ) : <div className="viewer-loading"><LoaderCircle className="spin" size={18} /> Загружаю Markdown…</div>}
                        </>
                      )}
                    </div>
                  )}
                </section>

                <section className="insights-section" aria-labelledby="insights-title">
                  <div className="section-heading">
                    <div><span className="section-eyebrow">АНАЛИЗ ДОКУМЕНТА</span><h2 id="insights-title">Ключевые ответы</h2></div>
                    <span className="insight-count">{insights.length || 7} тем</span>
                  </div>
                  <div className="insight-grid">
                    {insights.map((insight, index) => (
                      <article className={`insight-card ${index === 0 ? 'insight-overview' : ''}`} key={insight.id}>
                        <div className="insight-card-top"><span className="insight-number">{String(index + 1).padStart(2, '0')}</span><h3>{insight.question}</h3></div>
                        <p className={`insight-answer ${insight.citations.length === 0 ? 'no-evidence' : ''}`}><CitationText text={insight.answer} citations={insight.citations} onOpenSource={openSource} /></p>
                        <div className="insight-citations">
                          {insight.citations.length ? insight.citations.map((source) => (
                            <button key={source.id} className="citation-chip" data-source-id={source.id} onClick={() => void openSource(source)} title={source.text}>
                              {source.is_derived ? <Calculator size={12} /> : <BookOpen size={12} />}
                              <span>{locatorText(source)}</span><ChevronRight size={12} />
                            </button>
                          )) : <span className="no-citation">Подтверждение не найдено</span>}
                        </div>
                      </article>
                    ))}
                    {insights.length === 0 && <div className="insights-empty"><LoaderCircle className="spin" size={18} /> Загружаю карточки анализа…</div>}
                  </div>
                  <div className="citation-note"><ShieldCheck size={14} /><span>Ответы основаны на фрагментах документа. Нажмите на источник, чтобы открыть его место.</span></div>
                </section>
                </div>
              </div>
            )}
          </div>
        )}
      </main>

      {chatOpen && <div className="chat-resizer" role="separator" aria-label="Ширина чата" aria-orientation="vertical" aria-valuemin={320} aria-valuemax={560} aria-valuenow={chatWidth} tabIndex={0} onPointerDown={startResize} onKeyDown={resizeByKeyboard} />}
      <aside id="document-chat" className={`chat-panel ${chatFull ? 'chat-panel-full' : ''}`} aria-label="Чат по документу">
        <div className="chat-panel-header">
          <div className="chat-title"><span className="chat-title-icon"><MessageSquareText size={16} /></span><div><strong>Чат с документом</strong><span>{document?.status === 'ready' ? 'Ответы с источниками' : 'Ожидает документ'}</span></div></div>
          <div className="chat-header-actions">
            <button className="icon-button desktop-chat-size" aria-label={chatFull ? 'Вернуть панель чата' : 'Чат на всю рабочую область'} title={chatFull ? 'Вернуть панель чата' : 'Чат на всю рабочую область'} onClick={() => setChatFull((value) => !value)}>{chatFull ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>
          </div>
        </div>
        <div className="chat-context-line">
          <span className={`context-status-dot ${document?.status === 'ready' ? 'context-ready' : ''}`} />
          <span>{document?.filename ?? 'Загрузите документ, чтобы начать'}</span>
        </div>
        <div className="conversation" ref={conversation} aria-live="polite">
          {!document || document.status !== 'ready' ? (
            <div className="chat-empty-state"><span className="chat-empty-icon"><MessageSquareText size={21} /></span><strong>{document ? statusLabel(document.status) : 'Выберите документ'}</strong><p>{document ? processingDescription(document.status) : 'После загрузки файла здесь можно задавать вопросы и получать ответы с привязкой к источнику.'}</p></div>
          ) : messages.length === 0 && !isSending ? (
            <div className="chat-welcome">
              <span className="welcome-kicker">ГОТОВЫЙ К ВОПРОСАМ</span>
              <h3>С чего начнём?</h3>
              <p>Ответы будут основаны на тексте этого документа.</p>
              <div className="suggestion-list">
                {['Кратко перескажи документ', 'Кто отвечает за выполнение?', 'Какие сроки указаны?'].map((question) => <button key={question} type="button" onClick={() => { setChatInput(question); window.requestAnimationFrame(() => chatInputRef.current?.focus()) }}><span>{question}</span><ArrowUp size={14} /></button>)}
              </div>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message) => <ChatBubble key={message.id} message={message} onOpenSource={openSource} highlighted={message.id === highlightedMessageId} />)}
              {isSending && streamText && <ChatBubble message={{ id: 'streaming', role: 'assistant', content: streamingContent, citations: streamingCitations, created_at: new Date().toISOString() }} onOpenSource={openSource} />}
              {isSending && <div className="assistant-thinking"><span className="thinking-mark"><span /><span /><span /></span><span>{streamText ? 'Ответ формируется' : 'Сверяю ответ с фрагментами'}</span></div>}
            </div>
          )}
        </div>
        <div className="chat-compose-area">
          {!authReady && document?.status === 'ready' && <button className="codex-reminder" onClick={() => setAuthOpen(true)}><CircleHelp size={14} /> Подключите Codex, чтобы отправить вопрос <ChevronRight size={14} /></button>}
          <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); void sendMessage() }}>
            <textarea
              ref={chatInputRef}
              rows={2}
              name="chat-message"
              autoComplete="off"
              value={chatInput}
              onChange={(event) => setChatInput(event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void sendMessage() } }}
              placeholder={document?.status === 'ready' ? 'Задайте вопрос по документу…' : 'Чат станет доступен после обработки'}
              aria-label="Сообщение для чата"
              maxLength={4_000}
              disabled={!document || document.status !== 'ready' || !authReady || isSending}
            />
            <div className="composer-footer"><span>Ответы проверяются по источникам</span><button className="send-button" type="submit" aria-label="Отправить вопрос" disabled={!chatInput.trim() || !chat || !authReady || isSending}><Send size={15} /></button></div>
          </form>
          <p className="chat-footnote">Текст документа обрабатывается локально. В Codex передаются выбранные фрагменты.</p>
        </div>
      </aside>

      <button className="mobile-scrim" type="button" aria-label="Закрыть открытые панели" onClick={() => { setMobileLibraryOpen(false); setMobileChatOpen(false) }} />

      {exportOpen && document && <div className="modal-backdrop export-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeExportDialog() }}>
        <section className="modal-card export-dialog" role="dialog" aria-modal="true" aria-labelledby="export-title" aria-describedby="export-description">
          <button className="icon-button modal-close" data-modal-close="true" aria-label="Закрыть экспорт" onClick={closeExportDialog} disabled={exportPending}><X size={19} /></button>
          <span className="modal-eyebrow">СОХРАНЁННЫЕ РЕЗУЛЬТАТЫ</span>
          <h2 id="export-title">Экспорт документа</h2>
          <p id="export-description" className="modal-intro">Выберите, что включить в файл. Экспорт использует уже сохранённые ответы и не отправляет новый запрос модели.</p>

          <fieldset className="export-scope-fieldset">
            <legend>Содержимое</legend>
            <label className={`export-scope-option ${exportScope === 'analysis' ? 'is-selected' : ''}`}>
              <input type="radio" name="export-scope" value="analysis" checked={exportScope === 'analysis'} onChange={() => { setExportScope('analysis'); setExportError('') }} disabled={exportPending || insights.length === 0} />
              <span><strong>Полный анализ</strong><small>{insights.length} {pluralLabel(insights.length, 'ответ', 'ответа', 'ответов')} и источники</small></span>
            </label>
            <label className={`export-scope-option ${exportScope === 'selected_answers' ? 'is-selected' : ''}`}>
              <input type="radio" name="export-scope" value="selected_answers" checked={exportScope === 'selected_answers'} onChange={() => { setExportScope('selected_answers'); setExportError('') }} disabled={exportPending || insights.length === 0} />
              <span><strong>Выбранные ответы</strong><small>Отметьте нужные карточки анализа</small></span>
            </label>
            <label className={`export-scope-option ${exportScope === 'conversation' ? 'is-selected' : ''}`}>
              <input type="radio" name="export-scope" value="conversation" checked={exportScope === 'conversation'} onChange={() => { setExportScope('conversation'); setExportError('') }} disabled={exportPending || messages.length === 0} />
              <span><strong>Текущая переписка</strong><small>{messages.length} {pluralLabel(messages.length, 'сообщение', 'сообщения', 'сообщений')} в хронологическом порядке</small></span>
            </label>
          </fieldset>

          {exportScope === 'selected_answers' && <div className="export-answer-picker" aria-label="Выбор ответов для экспорта">
            <div className="export-picker-heading"><strong>Ответы</strong><div><button type="button" onClick={() => setExportSelectedKeys(insights.map((insight) => insight.key))} disabled={exportPending}>Все</button><span aria-hidden="true">·</span><button type="button" onClick={() => setExportSelectedKeys([])} disabled={exportPending}>Снять выбор</button></div></div>
            {insights.map((insight) => <label className="export-answer-option" key={insight.key}>
              <input type="checkbox" checked={exportSelectedKeys.includes(insight.key)} disabled={exportPending} onChange={(event) => setExportSelectedKeys((current) => event.target.checked ? [...current, insight.key] : current.filter((key) => key !== insight.key))} />
              <span><strong>{insight.question}</strong><small>{insight.answer.replace(/\s+/g, ' ').slice(0, 118)}{insight.answer.length > 118 ? '…' : ''}</small></span>
            </label>)}
            <p className="export-selection-count">Выбрано: {exportSelectedKeys.length} из {insights.length}</p>
          </div>}

          <fieldset className="export-format-fieldset">
            <legend>Формат файла</legend>
            <label className={`export-format-option ${exportFormat === 'pdf' ? 'is-selected' : ''}`}><input type="radio" name="export-format" value="pdf" checked={exportFormat === 'pdf'} onChange={() => setExportFormat('pdf')} disabled={exportPending} /><span><strong>PDF</strong><small>Для просмотра и печати</small></span></label>
            <label className={`export-format-option ${exportFormat === 'markdown' ? 'is-selected' : ''}`}><input type="radio" name="export-format" value="markdown" checked={exportFormat === 'markdown'} onChange={() => setExportFormat('markdown')} disabled={exportPending} /><span><strong>Markdown</strong><small>Для редактирования</small></span></label>
          </fieldset>

          <p className="export-source-note"><BookOpen size={14} /> Цитаты сохраняются с привязкой к странице, абзацу или строке документа.</p>
          {exportError && <p className="export-error" role="alert">{exportError}</p>}
          <div className="export-actions"><button className="button button-light" type="button" onClick={closeExportDialog} disabled={exportPending}>Отмена</button><button className="button button-dark" type="button" onClick={() => void downloadExport()} disabled={exportPending || (exportScope === 'selected_answers' && exportSelectedKeys.length === 0) || (exportScope === 'conversation' && messages.length === 0)}>{exportPending ? <><LoaderCircle className="spin" size={15} /> Готовлю файл…</> : <><Download size={15} /> Скачать {exportFormat === 'pdf' ? 'PDF' : 'Markdown'}</>}</button></div>
        </section>
      </div>}

      {authOpen && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setAuthOpen(false) }}>
        <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="auth-title" aria-describedby="auth-description">
          <button className="icon-button modal-close" data-modal-close="true" aria-label="Закрыть" onClick={() => setAuthOpen(false)}><X size={19} /></button>
          <span className="modal-symbol"><ShieldCheck size={21} /></span>
          <span className="modal-eyebrow">ПОДКЛЮЧЕНИЕ МОДЕЛИ</span>
          <h2 id="auth-title">Вход в Codex</h2>
          <p id="auth-description" className="modal-intro">Для анализа используется {codexModelLabel(codex)} с уровнем reasoning {codexReasoningLabel(codex?.reasoning_effort).toLowerCase()} через ваш аккаунт Codex. API-ключ не нужен.</p>
          <div className="auth-preferences" ref={authDetailsRef}>
            <div className="auth-details" aria-label="Настройки модели Codex">
            <div className="auth-detail-choice">
              <button
                ref={modelTriggerRef}
                className={`auth-detail-trigger ${openPreferenceMenu === 'model' ? 'is-open' : ''}`}
                type="button"
                aria-haspopup="listbox"
                aria-expanded={openPreferenceMenu === 'model'}
                aria-controls="codex-model-options"
                aria-label={`Модель: ${codexModelLabel(codex)}`}
                title={modelMenuDisabled ? 'Подключите аккаунт Codex, чтобы выбрать модель' : 'Выбрать модель'}
                disabled={modelMenuDisabled}
                onClick={() => togglePreferenceMenu('model')}
                onKeyDown={(event) => {
                  if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && openPreferenceMenu !== 'model') {
                    event.preventDefault()
                    togglePreferenceMenu('model')
                  }
                }}
              >
                <span className="auth-detail-label">Модель</span>
                <strong>{codexModelLabel(codex)}</strong>
                <ChevronDown className="auth-detail-arrow" size={15} aria-hidden="true" />
              </button>
            </div>
            <div className="auth-detail-choice">
              <button
                ref={reasoningTriggerRef}
                className={`auth-detail-trigger ${openPreferenceMenu === 'reasoning' ? 'is-open' : ''}`}
                type="button"
                aria-haspopup="listbox"
                aria-expanded={openPreferenceMenu === 'reasoning'}
                aria-controls="codex-reasoning-options"
                aria-label={`Уровень анализа: ${codexReasoningLabel(codex?.reasoning_effort)}`}
                title={reasoningMenuDisabled ? 'Выбор доступен после подключения модели' : 'Выбрать уровень анализа'}
                disabled={reasoningMenuDisabled}
                onClick={() => togglePreferenceMenu('reasoning')}
                onKeyDown={(event) => {
                  if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && openPreferenceMenu !== 'reasoning') {
                    event.preventDefault()
                    togglePreferenceMenu('reasoning')
                  }
                }}
              >
                <span className="auth-detail-label">Уровень анализа</span>
                <strong>{codexReasoningLabel(codex?.reasoning_effort)}</strong>
                <ChevronDown className="auth-detail-arrow" size={15} aria-hidden="true" />
              </button>
            </div>
            <div className="auth-detail-static"><span className="auth-detail-label">Состояние</span><strong>{authReady ? 'Подключено' : codex?.login_state === 'pending' ? 'Ожидание подтверждения' : 'Не подключено'}</strong></div>
            </div>
            {openPreferenceMenu === 'model' && <div id="codex-model-options" className="auth-detail-option-list auth-preference-list auth-preference-list-model" role="listbox" aria-label="Доступные модели">
              {codexModelOptions.map((option) => {
                const unavailable = option.id === codex?.model && !codex?.model_available
                return <button
                  key={option.id}
                  className={`auth-detail-option ${option.id === codex?.model ? 'is-selected' : ''}`}
                  type="button"
                  role="option"
                  aria-selected={option.id === codex?.model}
                  disabled={unavailable || codexSaving}
                  onClick={() => void selectCodexModel(option.id)}
                  onKeyDown={(event) => movePreferenceFocus('model', event)}
                >
                  <span className="auth-detail-option-copy"><strong>{option.label}</strong>{option.description && <small>{option.description}</small>}</span>
                  {option.id === codex?.model && <Check size={14} aria-hidden="true" />}
                </button>
              })}
            </div>}
            {openPreferenceMenu === 'reasoning' && <div id="codex-reasoning-options" className="auth-detail-option-list auth-preference-list auth-preference-list-reasoning" role="listbox" aria-label="Уровни анализа">
              {codexReasoningOptions.map((option) => <button
                key={option.value}
                className={`auth-detail-option ${option.value === codex?.reasoning_effort ? 'is-selected' : ''}`}
                type="button"
                role="option"
                aria-selected={option.value === codex?.reasoning_effort}
                disabled={codexSaving}
                onClick={() => void selectCodexReasoning(option.value)}
                onKeyDown={(event) => movePreferenceFocus('reasoning', event)}
              >
                <span className="auth-detail-option-copy"><strong>{option.label}</strong>{option.description && <small>{option.description}</small>}</span>
                {option.value === codex?.reasoning_effort && <Check size={14} aria-hidden="true" />}
              </button>)}
            </div>}
          </div>
          <p className="auth-settings-note">{codexSaving ? 'Сохраняю настройки…' : codex?.authenticated && codex?.models?.length ? 'Доступны модели и уровни из вашего аккаунта Codex.' : 'Подключите аккаунт, чтобы выбрать доступные варианты.'}</p>
          {codexPreferenceMessage && <p className="auth-settings-message" role="status">{codexPreferenceMessage}</p>}
          {codex?.login_state === 'pending' && codex.user_code ? (
            <div className="device-code-box">
              <span>Откройте страницу и введите код</span>
              <strong>{codex.user_code}</strong>
              {codex.verification_url && <a className="button button-dark auth-link" href={codex.verification_url} target="_blank" rel="noreferrer">Открыть вход в Codex <ChevronRight size={15} /></a>}
              <span className="device-wait"><LoaderCircle size={14} className="spin" /> Жду подтверждения…</span>
            </div>
          ) : authReady ? (
            <div className="auth-success"><Check size={16} /><span>Аккаунт подключён. История входа сохраняется локально.</span></div>
          ) : (
            <>
              <ol className="auth-steps"><li>Получите одноразовый код входа.</li><li>Откройте страницу Codex и подтвердите вход.</li><li>Вернитесь сюда — обработка продолжится автоматически.</li></ol>
              <button className="button button-dark auth-start" onClick={() => void beginLogin()}><ShieldCheck size={16} /> Войти через ChatGPT</button>
              {codex?.error && <p className="modal-error" role="alert">{codex.error}</p>}
            </>
          )}
          <p className="auth-storage-note"><ShieldCheck size={13} /> Данные авторизации хранятся в локальном Docker volume.</p>
        </section>
      </div>}

      {deleteTarget && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDeleteTarget(null) }}>
        <section className="modal-card confirm-card" role="dialog" aria-modal="true" aria-labelledby="delete-title" aria-describedby="delete-description">
          <button className="icon-button modal-close" data-modal-close="true" aria-label="Закрыть" onClick={() => setDeleteTarget(null)}><X size={19} /></button>
          <span className="modal-symbol"><Trash2 size={20} /></span>
          <span className="modal-eyebrow">УДАЛЕНИЕ ДОКУМЕНТА</span>
          <h2 id="delete-title">Удалить файл?</h2>
          <p id="delete-description" className="modal-intro">Будут удалены оригинал, индекс, карточки и история чата для «{deleteTarget.filename}».</p>
          <div className="confirm-actions"><button className="button button-light" onClick={() => setDeleteTarget(null)}>Отмена</button><button className="button button-dark" onClick={() => void confirmDelete()}>Удалить документ</button></div>
        </section>
      </div>}

      {toast && <div className="toast-message" role="status" aria-live="polite">{toast}</div>}
    </div>
  )
}

function processingDescription(status: DocumentRecord['status']): string {
  return {
    queued: 'Файл сохранён. Ожидаю свободный слот для обработки.',
    extracting: 'Извлекаю текст и размечаю исходные места фрагментов.',
    ocr: 'Распознаю страницы локально. Оригинальный PDF не изменяется.',
    indexing: 'Создаю локальный полнотекстовый и векторный индексы.',
    analyzing: 'Подбираю подтверждения и готовлю семь ответов.',
    ready: 'Документ проиндексирован и готов к вопросам.',
    needs_auth: 'Подключите аккаунт Codex, чтобы создать карточки и начать чат.',
    model_unavailable: 'Проверьте доступность выбранной модели для этого аккаунта.',
    cancelled: 'Обработка отменена. Можно запустить её повторно.',
    error: 'Можно проверить файл или повторить обработку.',
  }[status]
}

function EmptyWorkspace({
  isUploading,
  onChoose,
  documentsCount,
  onToggleLibrary,
  onExpandChat,
}: {
  isUploading: boolean
  onChoose: () => void
  documentsCount: number
  onToggleLibrary: () => void
  onExpandChat: () => void
}) {
  return (
    <div className="empty-workspace">
      <div className="empty-workspace-toolbar">
        <div className="chat-title">
          <span className="chat-title-icon"><MessageSquareText size={16} /></span>
          <div><strong>Чат с документом</strong><span>Ожидает документ</span></div>
        </div>
        <div className="chat-header-actions">
          <button className="icon-button" aria-label="Развернуть чат" title="Развернуть чат" onClick={onExpandChat}><Maximize2 size={16} /></button>
          <button className="icon-button" aria-label="Свернуть библиотеку" title="Свернуть библиотеку" onClick={onToggleLibrary}><PanelRightOpen size={16} /></button>
        </div>
      </div>
      <div className="empty-workspace-content">
        <span className="empty-eyebrow">АНАЛИЗ ДОКУМЕНТОВ</span>
        <h1>Загрузите документ.<br /><span>Задавайте вопросы по его содержанию.</span></h1>
        <p className="empty-description">Получайте ответы с цитатами и быстро находите нужное в тексте.</p>
        <button className="dropzone" onClick={onChoose} disabled={isUploading}>
          <span className="dropzone-icon">{isUploading ? <LoaderCircle className="spin" size={22} /> : <FileUp size={22} />}</span>
          <strong>{isUploading ? 'Сохраняю файл…' : 'Перетащите файл сюда'}</strong>
          <span>или нажмите, чтобы выбрать на компьютере</span>
          <small>PDF · DOCX · TXT · MD · CSV · XML · XLSX · XLS · PPTX · HTML · JSON · EPUB <i /> до 25 МБ</small>
        </button>
        <div className="empty-footnote"><ShieldCheck size={15} /><span>Оригиналы и индексы остаются на вашем компьютере</span></div>
        {documentsCount > 0 && <p className="empty-library-note">Выберите сохранённый чат слева, чтобы продолжить работу.</p>}
      </div>
      <div className="empty-chat-compose-area">
        <div className="empty-chat-composer">
          <textarea rows={1} name="chat-message" autoComplete="off" placeholder="Задайте вопрос по документу…" aria-label="Вопрос по документу" disabled />
          <div className="empty-chat-footer">
            <span>Чат станет доступен после загрузки документа</span>
            <div className="empty-chat-actions">
              <button className="icon-button" type="button" aria-label="Прикрепить документ" title="Прикрепить документ" onClick={onChoose}><Paperclip size={18} /></button>
              <button className="send-button" type="button" aria-label="Отправить вопрос" disabled><Send size={15} /></button>
            </div>
          </div>
        </div>
        <p className="empty-chat-footnote">Ответы будут сопровождаться цитатами из документа.</p>
      </div>
    </div>
  )
}

function ChatBubble({ message, onOpenSource, highlighted = false }: { message: ChatMessage; onOpenSource: (source: SourceRef) => Promise<void>; highlighted?: boolean }) {
  return (
    <article className={`chat-message ${message.role === 'user' ? 'user-message' : 'assistant-message'} ${highlighted ? 'search-result-highlight' : ''}`} data-message-id={message.id}>
      {message.role === 'assistant' && <span className="assistant-avatar"><span /><span /><span /><span /></span>}
      <div className="message-body">
        <div className="message-author">{message.role === 'user' ? 'Вы' : 'Document Checker'}</div>
        <div className="message-text">{message.role === 'assistant' ? <ChatMarkdown text={message.content} citations={message.citations} onOpenSource={onOpenSource} /> : <CitationText text={message.content} citations={message.citations} onOpenSource={onOpenSource} />}</div>
        {message.role === 'assistant' && message.citations.length > 0 && <div className="message-sources"><span>ИСТОЧНИКИ</span>{message.citations.map((source, index) => <button key={source.id} onClick={() => void onOpenSource(source)} title={source.text}><BookOpen size={12} /> {index + 1} · {locatorText(source)}</button>)}</div>}
      </div>
    </article>
  )
}

function CitationText({ text, citations, onOpenSource }: { text: string; citations: SourceRef[]; onOpenSource: (source: SourceRef) => Promise<void> }) {
  const segments: Array<string | { index: number }> = []
  const marker = /〔(\d+)〕/g
  let last = 0
  let match: RegExpExecArray | null
  while ((match = marker.exec(text)) !== null) {
    if (match.index > last) segments.push(text.slice(last, match.index))
    segments.push({ index: Number(match[1]) - 1 })
    last = match.index + match[0].length
  }
  if (last < text.length) segments.push(text.slice(last))
  return <>{segments.map((segment, index) => typeof segment === 'string' ? <span key={index}>{segment}</span> : (
    citations[segment.index] ? <button key={index} className="inline-citation" onClick={() => void onOpenSource(citations[segment.index])}>источник {segment.index + 1}</button> : null
  ))}</>
}

export default App
