import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const app = await readFile(resolve(root, 'src/App.tsx'), 'utf8')
const layoutSelector = await readFile(resolve(root, 'src/components/DocumentLayoutSelector.tsx'), 'utf8')
const chatMarkdown = await readFile(resolve(root, 'src/components/ChatMarkdown.tsx'), 'utf8')
const markdownViewer = await readFile(resolve(root, 'src/components/MarkdownViewer.tsx'), 'utf8')
const originalViewer = await readFile(resolve(root, 'src/components/OriginalDocumentViewer.tsx'), 'utf8')
const pdfOriginalViewer = await readFile(resolve(root, 'src/components/PdfOriginalViewer.tsx'), 'utf8')
const styles = await readFile(resolve(root, 'src/styles.css'), 'utf8')
const appHelpTargets = await readFile(resolve(root, 'src/appHelpUiTargets.ts'), 'utf8')
const searchToolbar = await readFile(resolve(root, 'src/components/DocumentSearchToolbar.tsx'), 'utf8')
const aiComposer = await readFile(resolve(root, 'src/components/AIComposer.tsx'), 'utf8')
const voiceLevelMeter = await readFile(resolve(root, 'src/components/VoiceLevelMeter.tsx'), 'utf8')
const localSpeechInput = await readFile(resolve(root, 'src/useLocalSpeechInput.ts'), 'utf8')
const microphoneLevel = await readFile(resolve(root, 'src/microphoneLevel.mjs'), 'utf8')
const backendAppHelp = await readFile(resolve(root, '../backend/app/services/app_help.py'), 'utf8')

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

test('document comparison action follows the sidebar button design and remains usable when collapsed', () => {
  assert.match(app, /className="library-compare-add"[^>]*aria-label="Сравнить документы"/)
  assert.match(styles, /\.library-compare-add\s*\{[^}]*border-radius:\s*999px/)
  assert.match(styles, /\.library-compare-add\s*\{[^}]*border:\s*1px solid var\(--line\)/)
  assert.match(styles, /\.library-compare-add:hover:not\(:disabled\)/)
  assert.match(styles, /\.library-manual-collapsed\s+\.library-compare-add\s+span\s*\{\s*display:\s*none/)
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

test('application help UI targets are an exact versioned mirror of the backend catalog', () => {
  const frontendIds = [...appHelpTargets.matchAll(/^\s*'([a-z.]+)',?$/gm)].map(match => match[1])
  const backendBlock = backendAppHelp.match(/APP_HELP_UI_TARGETS\s*=\s*frozenset\(\{([\s\S]*?)\}\)/)?.[1]
  assert.ok(backendBlock, 'Backend target allowlist should exist')
  const backendIds = [...backendBlock.matchAll(/"([a-z.]+)"/g)].map(match => match[1])
  const frontendVersion = appHelpTargets.match(/APP_HELP_CATALOG_VERSION\s*=\s*'([^']+)'/)?.[1]
  const backendVersion = backendAppHelp.match(/APP_HELP_CATALOG_VERSION\s*=\s*"([^"]+)"/)?.[1]
  assert.deepEqual(frontendIds.sort(), backendIds.sort())
  assert.equal(frontendVersion, backendVersion)
})

test('UI help only highlights a registered current-build target after an explicit accessible action', () => {
  assert.match(appHelpTargets, /useAppHelpTargetRef/)
  assert.match(appHelpTargets, /new Map<AppHelpUiTargetId, Set<HTMLElement>>/)
  assert.match(appHelpTargets, /available\.length > 1/)
  assert.match(appHelpTargets, /aria-hidden/)
  assert.match(appHelpTargets, /matches\(':disabled'\)/)
  assert.match(appHelpTargets, /aria-busy/)
  assert.match(appHelpTargets, /elementFromPoint/)
  assert.match(appHelpTargets, /setAttribute\('data-help-highlighted', 'true'\)/)
  assert.match(appHelpTargets, /1_800/)
  assert.match(app, /buildCheck\.kind === 'matched'/)
  assert.match(app, /message\.ui_target_catalog_version === APP_HELP_CATALOG_VERSION/)
  assert.match(app, /message\.ui_target_build_id === buildCheck\.api\.build_id/)
  assert.match(app, /Показать в интерфейсе/)
  assert.match(app, /role="status" aria-live="polite" aria-atomic="true"/)
  assert.match(app, /element\.scrollIntoView\([\s\S]*behavior: reducedMotion \? 'instant' : 'smooth'/)
  assert.doesNotMatch(appHelpTargets, /\.click\(\)|focus\(\)|querySelector\(/)
  assert.match(styles, /\[data-help-target\]\[data-help-highlighted="true"\]/)
  assert.match(styles, /\[data-help-target\]\s*\{[^}]*var\(--duration-state\)/)
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/)
  assert.match(styles, /\[data-help-target\]\s*\{\s*transition: none;/)
})

test('confirmed user-facing targets are registered at their actual controls', () => {
  for (const targetId of [
    'document.upload.open', 'chat-library.toggle', 'chat.new', 'codex.settings.open',
    'document.original.open', 'document.markdown.open',
    'document.bookmarks.open', 'document.export.open', 'local-data.open',
  ]) assert.match(app, new RegExp(`data-help-target="${targetId.replaceAll('.', '\\.') }"`))
  assert.match(layoutSelector, /data-help-target="workspace\.layout\.select"/)
  assert.match(app, /data-help-target=\{index === 0 && insight\.citations\.length > 0 \? 'document\.citations\.open'/)
  assert.match(searchToolbar, /data-help-target="document\.search\.open"/)
  assert.match(originalViewer, /data-help-target="document\.ocr\.settings"/)
  assert.match(originalViewer, /data-help-target="document\.table\.controls"/)
})

test('new chat opens the empty workspace instead of the file picker', () => {
  assert.match(app, /NEW_CHAT_STORAGE_KEY\s*=\s*'document-checker-new-chat'/)
  assert.match(app, /useState\(\(\) => localStorage\.getItem\(NEW_CHAT_STORAGE_KEY\) === 'true'/)
  assert.match(app, /const startNewChat = useCallback\(\(\) => \{[\s\S]*setNewChatOpen\(true\)[\s\S]*setSelectedId\(null\)[\s\S]*setChatOpen\(true\)/)
  const newChatButton = app.match(/<button(?=[^>]*className="library-add")[^>]*>[\s\S]*?<\/button>/)?.[0]
  assert.ok(newChatButton, 'New chat button should be present')
  assert.match(newChatButton, /onClick=\{startNewChat\}/)
  assert.doesNotMatch(newChatButton, /fileInput\.current\?\.click\(\)/)
  assert.match(app, /<div className="empty-workspace">/)
  assert.match(app, /Загрузите документ\.<br \/>/)
})

test('AI composers share accessible attachment, model, voice, send, and keyboard behavior', () => {
  assert.match(app, /<AIComposer[\s\S]*?onAttach=\{\(\) => fileInput\.current\?\.click\(\)\}[\s\S]*?onOpenModelSettings=\{\(\) => setAuthOpen\(true\)\}[\s\S]*?voiceInputEnabled=\{chatVisible\}/)
  assert.doesNotMatch(app, /onVoiceInput|Функция голосового ввода находится в разработке/)
  assert.match(app, /<AIComposer[\s\S]*?inputLabel="Вопрос по документу"[\s\S]*?inputDisabled[\s\S]*?sendDisabled/)
  assert.match(aiComposer, /className=\{recording \? 'ai-composer is-recording' : 'ai-composer'\}/)
  assert.match(aiComposer, /aria-label="Прикрепить документ"/)
  assert.match(aiComposer, /aria-label=\{`Модель \$\{modelName\}, уровень анализа \$\{reasoningName\}\. Настроить`\}/)
  assert.match(aiComposer, /aria-pressed=\{voiceActive\}/)
  assert.match(aiComposer, /aria-live="polite"/)
  assert.match(aiComposer, /aria-keyshortcuts=\{voiceActive \? 'Escape' : undefined\}/)
  assert.match(aiComposer, /aria-label="Отменить диктовку"/)
  assert.match(aiComposer, /aria-label="Завершить диктовку"/)
  assert.match(aiComposer, /<VoiceLevelMeter analyser=\{voiceInput\.meterAnalyser\} recording=\{voiceInput\.meterAvailability === 'available'\}/)
  assert.match(aiComposer, /data-level-available=\{voiceInput\.meterAvailability === 'available'\}/)
  assert.match(aiComposer, /stopVoiceRef\.current\?\.focus/)
  assert.match(aiComposer, /textareaRef\.current\?\.focus/)
  assert.match(aiComposer, /aria-label="Отправить вопрос"/)
  assert.match(aiComposer, /event\.key !== 'Enter' \|\| event\.shiftKey \|\| event\.nativeEvent\.isComposing/)
  assert.match(aiComposer, /maxHeight = 120/)
  assert.match(styles, /--radius-composer:\s*22px/)
  assert.match(styles, /@container \(max-width: 420px\)/)
  assert.match(styles, /\.ai-composer-send:hover:not\(:disabled\)/)
  assert.match(styles, /\.ai-composer, \.ai-composer button \{ transition: none; \}/)
  assert.match(styles, /@keyframes composer-voice-enter/)
  assert.match(styles, /\.ai-composer-recording \{ animation: none; \}/)
  assert.match(voiceLevelMeter, /requestAnimationFrame/)
  assert.match(voiceLevelMeter, /cancelAnimationFrame/)
  assert.match(voiceLevelMeter, /prefers-reduced-motion: reduce/)
  assert.match(voiceLevelMeter, /REDUCED_SAMPLE_INTERVAL_MS/)
  assert.match(voiceLevelMeter, /if \(reducedMotion\)/)
  assert.match(voiceLevelMeter, /aria-hidden="true"/)
  assert.match(localSpeechInput, /Recognition\.available\(\{[\s\S]*processLocally: true[\s\S]*quality: 'dictation'/)
  assert.match(localSpeechInput, /Recognition\.install\(\{[\s\S]*processLocally: true[\s\S]*quality: 'dictation'/)
  assert.match(localSpeechInput, /recognition\.processLocally = true/)
  assert.match(localSpeechInput, /recognition\.processLocally = false/)
  assert.match(localSpeechInput, /startBrowserRecognition/)
  assert.match(localSpeechInput, /recognition\.abort\(\)/)
  assert.match(localSpeechInput, /document\.visibilityState === 'hidden'/)
  assert.match(localSpeechInput, /openLocalMicrophoneAnalyzer\(\)/)
  assert.match(localSpeechInput, /meterMonitor\.dispose\(\)/)
  assert.match(localSpeechInput, /mode === 'local'[\s\S]*processLocally: false[\s\S]*startBrowserRecognition/)
  assert.doesNotMatch(localSpeechInput, /MediaRecorder|fetch\(/)
  assert.match(microphoneLevel, /getUserMedia\(\{ audio: true, video: false \}\)/)
  assert.match(microphoneLevel, /source\.connect\(analyser\)/)
  assert.doesNotMatch(microphoneLevel, /MediaRecorder|fetch\(|localStorage|\.destination/)
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

test('frontend uploads and labels derive supported formats from the capability registry', () => {
  assert.match(app, /ACCEPTED_DOCUMENT_EXTENSIONS\.join/)
  assert.match(app, /SUPPORTED_DOCUMENT_EXTENSION_SET\.has/)
  assert.match(app, /SUPPORTED_FORMAT_LABELS/)
})

test('OCR has an explicit processing state and user-facing fallback message', () => {
  assert.match(app, /ocr: 'Распознаю скан'/)
  assert.match(app, /document\.ocr_status === 'partial'/)
  assert.match(originalViewer, /Создаю карту координат/)
  assert.match(app, /Не удалось распознать скан/)
  assert.match(styles, /\.ocr-notice\s*\{[^}]*border/)
  assert.match(pdfOriginalViewer, /pdfOcrWordBoxes\(selectedSource\)/)
  assert.match(pdfOriginalViewer, /pdf-ocr-highlight-box/)
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
