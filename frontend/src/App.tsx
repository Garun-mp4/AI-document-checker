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
  FileCode2,
  FileSpreadsheet,
  FileText,
  FileUp,
  LoaderCircle,
  Maximize2,
  Menu,
  MessageSquareText,
  Minimize2,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  RotateCw,
  Send,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react'
import type { ChatMessage, ChatRecord, ChatSummary, CodexStatus, DocumentPreview, DocumentRecord, Insight, MarkdownDocument, SourceRef, StreamCitation } from './types'
import { OriginalDocumentViewer } from './components/OriginalDocumentViewer'
import { MarkdownViewer } from './components/MarkdownViewer'
import { ChatMarkdown } from './components/ChatMarkdown'

const API = '/api/v1'
const ACCEPTED = '.pdf,.docx,.txt,.md,.csv,.xml,.xlsx,.xls,.pptx,.html,.htm,.json,.epub'
const SELECTED_CHAT_STORAGE_KEY = 'document-checker-selected-chat'
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'document-checker-sidebar-collapsed'
const DEFAULT_CODEX_MODEL = 'gpt-6-luna'
const DEFAULT_CODEX_REASONING = 'medium'

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
    indexing: 'Создаю индекс',
    analyzing: 'Готовлю ответы',
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

function summaryToDocument(summary: ChatSummary): DocumentRecord {
  return {
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
    created_at: summary.created_at,
    updated_at: summary.last_activity_at,
  }
}

function App() {
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(() => localStorage.getItem(SELECTED_CHAT_STORAGE_KEY))
  const [document, setDocument] = useState<DocumentRecord | null>(null)
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
  const [documentPreview, setDocumentPreview] = useState<DocumentPreview | null>(null)
  const [markdownDocument, setMarkdownDocument] = useState<MarkdownDocument | null>(null)
  const [previewTab, setPreviewTab] = useState<'original' | 'markdown'>('original')
  const [markdownRebuilding, setMarkdownRebuilding] = useState(false)
  const [markdownLoadingMore, setMarkdownLoadingMore] = useState(false)
  const [previewPage, setPreviewPage] = useState(1)
  const fileInput = useRef<HTMLInputElement>(null)
  const chatInputRef = useRef<HTMLTextAreaElement>(null)
  const workArea = useRef<HTMLElement>(null)
  const conversation = useRef<HTMLDivElement>(null)
  const authDetailsRef = useRef<HTMLDivElement>(null)
  const modelTriggerRef = useRef<HTMLButtonElement>(null)
  const reasoningTriggerRef = useRef<HTMLButtonElement>(null)
  const resizeState = useRef<{ startX: number; startWidth: number } | null>(null)

  const showToast = useCallback((message: string) => {
    setToast(message)
    window.setTimeout(() => setToast(''), 4_500)
  }, [])

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

  const refreshLibrary = useCallback(async () => {
    try {
      const result = await api<ChatSummary[]>(`${API}/chats`)
      setChats(result)
      setSelectedId((current) => current && result.some((item) => item.document_id === current) ? current : result[0]?.document_id ?? null)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось загрузить библиотеку документов.')
    }
  }, [showToast])

  useEffect(() => {
    void refreshLibrary()
    const timer = window.setInterval(() => void refreshLibrary(), 8_000)
    return () => window.clearInterval(timer)
  }, [refreshLibrary])

  useEffect(() => {
    if (selectedId) localStorage.setItem(SELECTED_CHAT_STORAGE_KEY, selectedId)
    else localStorage.removeItem(SELECTED_CHAT_STORAGE_KEY)
  }, [selectedId])

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
      setInsights([])
      setDocumentPreview(null)
      setMarkdownDocument(null)
      setChat(null)
      setMessages([])
      return
    }
    let active = true
    let loaded = false
    setDocument(null)
    setInsights([])
    setDocumentPreview(null)
    setMarkdownDocument(null)
    setPreviewTab('original')
    setChat(null)
    setMessages([])
    setSelectedSourceId(null)
    setSelectedSource(null)
    const refresh = async () => {
      try {
        const result = await api<DocumentRecord>(`${API}/documents/${selectedId}`)
        if (!active) return
        setDocument(result)
        updateDocumentInLibrary(result)
        if (result.status === 'ready' && !loaded) {
          loaded = true
          await loadReadyData(selectedId)
        }
      } catch (error) {
        if (active) showToast(error instanceof Error ? error.message : 'Не удалось открыть документ.')
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
        if (openPreferenceMenu) {
          event.preventDefault()
          setOpenPreferenceMenu(null)
          window.requestAnimationFrame(() => {
            const trigger = openPreferenceMenu === 'model' ? modelTriggerRef.current : reasoningTriggerRef.current
            trigger?.focus()
          })
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
  }, [openPreferenceMenu])

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
    if (!authOpen && !deleteTarget) return
    const dialog = window.document.querySelector<HTMLElement>('.modal-card')
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
  }, [authOpen, deleteTarget])

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
      setSelectedId(created.id)
      void refreshLibrary()
      setPreviewOpen(true)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось загрузить документ.')
    } finally {
      setIsUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }, [refreshLibrary, showToast])

  const openSource = useCallback(async (source: SourceRef | StreamCitation) => {
    if (!document) return
    setPreviewOpen(true)
    setPreviewTab('original')
    setSelectedSourceId(source.id)
    setSelectedSource(source)
    const page = source.locator.page
    if (typeof page === 'number' && page > 0) setPreviewPage(page)
    window.requestAnimationFrame(() => {
      window.document.getElementById('document-original-viewer')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
    setMobileChatOpen(false)
  }, [document])

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

  const retryDocument = useCallback(async () => {
    if (!document) return
    try {
      const updated = await api<DocumentRecord>(`${API}/documents/${document.id}/retry`, { method: 'POST' })
      setDocument(updated)
      updateDocumentInLibrary(updated)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось повторить обработку.')
    }
  }, [document, showToast, updateDocumentInLibrary])

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
      if (selectedId === deleteTarget.id) setSelectedId(null)
      setDeleteTarget(null)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Не удалось удалить документ.')
    }
  }, [deleteTarget, selectedId, showToast])

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
  const catalogModelOptions = codex?.models ?? []
  const unavailableCurrentModel = codex?.model && catalogModelOptions.length && !catalogModelOptions.some((item) => item.id === codex.model) ? [{
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
  const modelMenuDisabled = !codex?.authenticated || !codex?.models?.length || codexSaving
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
  const activeStatus = visibleStatus ? ['queued', 'extracting', 'indexing', 'analyzing'].includes(visibleStatus.status) : false
  const mainClasses = [
    'app-shell',
    sidebarCollapsed ? 'library-manual-collapsed' : '',
    !selectedId ? 'empty-state' : '',
    !chatOpen ? 'chat-hidden' : '',
    chatFull ? 'chat-full' : '',
    mobileLibraryOpen ? 'mobile-library-open' : '',
    mobileChatOpen ? 'mobile-chat-open' : '',
  ].filter(Boolean).join(' ')

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
          <button className="icon-button mobile-chat-toggle" aria-label="Открыть чат" aria-expanded={mobileChatOpen} aria-controls="document-chat" onClick={() => { setChatOpen(true); setMobileChatOpen(true) }}><MessageSquareText size={18} /></button>
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
        <button className="library-add" title="Новый чат" aria-label="Новый чат" onClick={() => fileInput.current?.click()} disabled={isUploading}>
          {isUploading ? <LoaderCircle className="spin" size={17} /> : <FileUp size={17} />}
          <span>Новый чат</span>
        </button>
        <div className="library-list-heading">
          <span>Недавние чаты</span><span className="count-badge">{chats.length}</span>
        </div>
        <div className="document-list">
          {chats.length === 0 ? (
            <div className="library-empty">Чаты с документами появятся здесь</div>
          ) : chats.map((item) => (
            <div key={item.id} className={`document-row ${selectedId === item.document_id ? 'selected' : ''}`}>
              <button className="document-select" onClick={() => { setSelectedId(item.document_id); setMobileLibraryOpen(false) }} title={item.filename} aria-label={`Открыть чат ${item.title}`}>
                <span className="document-type-icon">{fileIcon(item.file_type, 17)}</span>
                <span className="document-row-text">
                  <span className="document-row-name">{item.title}</span>
                  <span className="document-row-meta"><span className={`status-dot status-${item.status}`} />{item.message_count ? `${item.message_count} ${pluralLabel(item.message_count, 'сообщение', 'сообщения', 'сообщений')}` : statusLabel(item.status)}<span className="row-meta-divider">·</span>{relativeDate(item.last_activity_at)}</span>
                </span>
              </button>
              <button className="row-delete icon-button" aria-label={`Удалить ${item.filename}`} title={`Удалить ${item.filename}`} onClick={() => setDeleteTarget(summaryToDocument(item))}><Trash2 size={15} /></button>
            </div>
          ))}
        </div>
        <div className="library-footer">
          <span className="local-lock"><ShieldCheck size={14} /> История и документы хранятся локально</span>
          <span>до 25 МБ на файл</span>
        </div>
      </aside>

      <main id="main-content" tabIndex={-1} className={`workspace ${uploadActive ? 'drop-active' : ''}`} ref={workArea} onDragOver={(event) => { event.preventDefault(); setUploadActive(true) }} onDragLeave={(event) => { if (event.currentTarget === event.target) setUploadActive(false) }} onDrop={handleDrop}>
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
                <span className={`status-pill status-pill-${visibleStatus.status}`}>
                  {activeStatus && <LoaderCircle size={13} className="spin" />}
                  {visibleStatus.status === 'ready' && <Check size={13} />}
                  {statusLabel(visibleStatus.status)}
                </span>
                {visibleStatus.status === 'error' && <button className="icon-button" title="Повторить обработку" aria-label="Повторить обработку" onClick={() => void retryDocument()}><RotateCw size={16} /></button>}
                <button className="icon-button header-chat-toggle" aria-label={chatOpen ? 'Свернуть чат' : 'Открыть чат'} onClick={() => setChatOpen((value) => !value)}>
                  {chatOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
                </button>
              </div>
            </div>

            {activeStatus && (
              <div className="processing-banner" role="status" aria-live="polite">
                <div className="processing-spinner"><LoaderCircle size={19} className="spin" /></div>
                <div><strong>{statusLabel(visibleStatus.status)}</strong><span>{processingDescription(visibleStatus.status)}</span></div>
                <span className="processing-step">{visibleStatus.status === 'queued' ? '01' : visibleStatus.status === 'extracting' ? '02' : visibleStatus.status === 'indexing' ? '03' : '04'} / 04</span>
              </div>
            )}

            {(visibleStatus.status === 'needs_auth' || visibleStatus.status === 'model_unavailable' || visibleStatus.status === 'error') && (
              <div className={`issue-banner ${visibleStatus.status === 'error' ? 'issue-error' : ''}`} role="alert">
                <CircleHelp size={19} />
                <div className="issue-copy"><strong>{visibleStatus.status === 'needs_auth' ? 'Подключите Codex, чтобы получить ответы' : visibleStatus.status === 'model_unavailable' ? 'Выбранная модель недоступна' : 'Не удалось обработать документ'}</strong><span>{visibleStatus.error_message ?? 'Проверьте настройки и повторите действие.'}</span></div>
                {visibleStatus.status === 'needs_auth' ? <button className="button button-dark" onClick={() => setAuthOpen(true)}>Подключить</button> : <button className="button button-light" onClick={() => void retryDocument()}><RotateCw size={15} /> Повторить</button>}
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
                            <button type="button" role="tab" aria-selected={previewTab === 'original'} className={`preview-tab ${previewTab === 'original' ? 'is-active' : ''}`} onClick={() => setPreviewTab('original')}>Оригинал</button>
                            <button type="button" role="tab" aria-selected={previewTab === 'markdown'} className={`preview-tab ${previewTab === 'markdown' ? 'is-active' : ''}`} onClick={() => setPreviewTab('markdown')}>Markdown</button>
                            {markdownDocument?.status === 'ready' && <a className="preview-download" href={`${API}/documents/${document.id}/markdown/download`} download>Скачать .md</a>}
                          </div>
                          {previewTab === 'original' ? (
                            documentPreview.original_url ? <OriginalDocumentViewer document={document} preview={documentPreview} selectedSource={selectedSource} selectedSourceId={selectedSourceId} originalUrl={documentPreview.original_url} pageNumber={previewPage} /> : <div className="preview-render-error" role="alert"><TriangleAlert size={18} /><span>Оригинальный файл недоступен.</span></div>
                          ) : markdownDocument ? (
                            <MarkdownViewer data={markdownDocument} selectedSource={selectedSource} onRebuild={() => void rebuildMarkdown()} rebuilding={markdownRebuilding} onLoadMore={() => void loadMoreMarkdown()} loadingMore={markdownLoadingMore} />
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
                            <button key={source.id} className="citation-chip" onClick={() => void openSource(source)} title={source.text}>
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
            <button className="icon-button desktop-chat-size" aria-label={chatFull ? 'Свернуть чат' : 'Развернуть чат'} title={chatFull ? 'Вернуть панель' : 'На всю рабочую область'} onClick={() => setChatFull((value) => !value)}>{chatFull ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>
            <button className="icon-button desktop-chat-size" aria-label="Свернуть чат" title="Свернуть чат" onClick={() => { setChatOpen(false); setChatFull(false) }}><PanelRightClose size={16} /></button>
            <button className="icon-button close-mobile-panel" aria-label="Закрыть чат" onClick={() => setMobileChatOpen(false)}><X size={18} /></button>
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
              {messages.map((message) => <ChatBubble key={message.id} message={message} onOpenSource={openSource} />)}
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
    indexing: 'Создаю локальный полнотекстовый и векторный индексы.',
    analyzing: 'Подбираю подтверждения и готовлю семь ответов.',
    ready: 'Документ проиндексирован и готов к вопросам.',
    needs_auth: 'Подключите аккаунт Codex, чтобы создать карточки и начать чат.',
    model_unavailable: 'Проверьте доступность выбранной модели для этого аккаунта.',
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

function ChatBubble({ message, onOpenSource }: { message: ChatMessage; onOpenSource: (source: SourceRef) => Promise<void> }) {
  return (
    <article className={`chat-message ${message.role === 'user' ? 'user-message' : 'assistant-message'}`}>
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
