import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const app = await readFile(resolve(root, 'src/App.tsx'), 'utf8')
const chatMarkdown = await readFile(resolve(root, 'src/components/ChatMarkdown.tsx'), 'utf8')
const markdownViewer = await readFile(resolve(root, 'src/components/MarkdownViewer.tsx'), 'utf8')
const originalViewer = await readFile(resolve(root, 'src/components/OriginalDocumentViewer.tsx'), 'utf8')
const styles = await readFile(resolve(root, 'src/styles.css'), 'utf8')

test('sidebar collapse has persistent state and an accessible control contract', () => {
  assert.match(app, /SIDEBAR_COLLAPSED_STORAGE_KEY\s*=\s*'document-checker-sidebar-collapsed'/)
  assert.match(app, /useState\(\(\) => localStorage\.getItem\(SIDEBAR_COLLAPSED_STORAGE_KEY\)/)
  assert.match(app, /localStorage\.setItem\(SIDEBAR_COLLAPSED_STORAGE_KEY, String\(sidebarCollapsed\)\)/)
  assert.match(app, /id="chat-library"/)
  assert.match(app, /aria-controls="chat-library"/)
  assert.match(app, /Свернуть библиотеку/)
  assert.match(app, /Развернуть библиотеку/)
  assert.match(styles, /\.app-shell\.library-manual-collapsed\s*\{[^}]*72px/)
})

test('model picker exposes only the two supported Codex models', () => {
  assert.match(app, /ALLOWED_CODEX_MODELS\s*=\s*new Set\(\['gpt-6-luna',\s*'gpt-6\.1-sol'\]\)/)
  assert.match(app, /filter\(\(item\) => ALLOWED_CODEX_MODELS\.has\(item\.id\.toLowerCase\(\)\)\)/)
})

test('chat visibility has one persistent control outside the chat panel', () => {
  assert.match(app, /className="icon-button chat-visibility-toggle"/)
  assert.match(app, /aria-label=\{chatVisible \? 'Свернуть чат' : 'Открыть чат'\}/)
  assert.match(app, /aria-controls="document-chat"/)
  assert.doesNotMatch(app, /header-chat-toggle/)
  assert.doesNotMatch(app, /mobile-chat-toggle/)
})

test('new chat opens the empty workspace instead of the file picker', () => {
  assert.match(app, /NEW_CHAT_STORAGE_KEY\s*=\s*'document-checker-new-chat'/)
  assert.match(app, /useState\(\(\) => localStorage\.getItem\(NEW_CHAT_STORAGE_KEY\) === 'true'/)
  assert.match(app, /const startNewChat = useCallback\(\(\) => \{[\s\S]*setNewChatOpen\(true\)[\s\S]*setSelectedId\(null\)[\s\S]*setChatOpen\(true\)/)
  const newChatButton = app.match(/<button className="library-add"[\s\S]*?<\/button>/)?.[0]
  assert.ok(newChatButton, 'New chat button should be present')
  assert.match(newChatButton, /onClick=\{startNewChat\}/)
  assert.doesNotMatch(newChatButton, /fileInput\.current\?\.click\(\)/)
  assert.match(app, /<div className="empty-workspace">/)
  assert.match(app, /Загрузите документ\.<br \/>/)
})

test('empty workspace fits the desktop viewport without page scrolling', () => {
  assert.match(app, /workspace-empty/)
  assert.match(styles, /\.workspace\.workspace-empty\s*\{[^}]*overflow:\s*hidden/)
  assert.match(styles, /\.empty-workspace\s*\{[^}]*height:\s*100%[^}]*min-height:\s*0/)
  assert.match(styles, /\.dropzone\s*\{[^}]*min-height:\s*clamp\(/)
})

test('chat markdown renders GFM safely and keeps citation actions interactive', () => {
  assert.match(chatMarkdown, /remarkPlugins=\{\[remarkGfm, remarkCitations\]\}/)
  assert.match(chatMarkdown, /rehypePlugins=\{\[rehypeSanitize\]\}/)
  assert.match(chatMarkdown, /skipHtml/)
  assert.match(chatMarkdown, /className="inline-citation"/)
  assert.doesNotMatch(chatMarkdown, /dangerouslySetInnerHTML/)
})

test('document viewer exposes bounded original and Markdown modes', () => {
  assert.match(app, /Оригинал/)
  assert.match(app, />Markdown<\/button>/)
  assert.match(app, /OriginalDocumentViewer/)
  assert.match(app, /MarkdownViewer/)
  assert.match(markdownViewer, /markdown-line-active/)
  assert.match(markdownViewer, /aria-label="Markdown-представление документа"/)
  assert.match(styles, /\.markdown-viewer\s*\{[^}]*height:\s*clamp\(/)
  assert.match(styles, /\.markdown-viewer\s*\{[^}]*overflow:\s*auto/)
})

test('DOCX rendering isolates stale imperative renders from React nodes', () => {
  assert.match(originalViewer, /new AbortController\(\)/)
  assert.match(originalViewer, /document\.createElement\('div'\)/)
  assert.match(originalViewer, /renderAsync\(blob, renderHost/)
  assert.match(originalViewer, /container\.replaceChildren\(\.\.\.Array\.from\(renderHost\.childNodes\)\)/)
  assert.match(originalViewer, /new ResizeObserver\(fitPages\)/)
  assert.match(originalViewer, /page\.style\.zoom = String\(scale\)/)
  assert.match(originalViewer, /controller\.abort\(\)/)
})

test('table viewer ignores stale pagination and citation requests', () => {
  assert.match(originalViewer, /const requestRef = useRef\(0\)/)
  assert.match(originalViewer, /const requestId = \+\+requestRef\.current/)
  assert.match(originalViewer, /new AbortController\(\)/)
  assert.match(originalViewer, /signal: controller\.signal/)
  assert.match(originalViewer, /requestId !== requestRef\.current/)
})

test('frontend advertises the formats supported by the backend contract', () => {
  for (const extension of ['.pdf', '.docx', '.txt', '.md', '.csv', '.xml', '.xlsx', '.xls', '.pptx', '.html', '.json', '.epub']) {
    assert.match(app, new RegExp(`\\${extension}`))
  }
})

test('OCR has an explicit processing state and user-facing fallback message', () => {
  assert.match(app, /ocr: 'Распознаю скан'/)
  assert.match(app, /document\.ocr_status === 'partial'/)
  assert.match(originalViewer, /Создаю карту координат/)
  assert.match(app, /Не удалось распознать скан/)
  assert.match(styles, /\.ocr-notice\s*\{[^}]*border/)
  assert.match(originalViewer, /pdfOcrWordBoxes\(selectedSource\)/)
  assert.match(originalViewer, /pdf-ocr-highlight-box/)
})

test('OCR retry keeps language, quality, scope, impact and page confidence explicit', () => {
  for (const language of ['Русский', 'Английский', 'Русский + английский']) assert.ok(originalViewer.includes(language))
  for (const dpi of ['150', '200', '300']) assert.match(originalViewer, new RegExp(`${dpi} DPI`))
  assert.match(originalViewer, /ocr-page-status-list/)
  assert.match(originalViewer, /эвристика, а не вероятность правильного распознавания/)
  assert.match(originalViewer, /Создать новую версию OCR\?/)
  assert.match(originalViewer, /Оригинал документа и история чата сохранятся/)
  assert.match(originalViewer, /pages: number\[\] \| null/)
  assert.match(app, /\/ocr\/reprocess/)
  assert.match(styles, /\.ocr-impact-dialog::backdrop/)
  assert.match(styles, /\.ocr-pages-select\s*\{[^}]*max-height/)
})

test('OCR automatic scope reflects pages that actually need OCR and keeps manual retry available', () => {
  assert.match(originalViewer, /Все страницы, для которых нужен OCR \(\{eligiblePages\.length\}\)/)
  assert.match(originalViewer, /eligiblePages\.length === 0/)
  assert.match(originalViewer, /Страниц, которым требуется OCR, не найдено/)
  assert.match(originalViewer, /disabled=\{page\.classification === 'blank' \|\| page\.ocrResult === 'blank'\}/)
})
