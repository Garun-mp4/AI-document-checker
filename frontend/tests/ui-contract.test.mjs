import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const app = await readFile(resolve(root, 'src/App.tsx'), 'utf8')
const chatMarkdown = await readFile(resolve(root, 'src/components/ChatMarkdown.tsx'), 'utf8')
const markdownViewer = await readFile(resolve(root, 'src/components/MarkdownViewer.tsx'), 'utf8')
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

test('frontend advertises the formats supported by the backend contract', () => {
  for (const extension of ['.pdf', '.docx', '.txt', '.md', '.csv', '.xml', '.xlsx', '.xls', '.pptx', '.html', '.json', '.epub']) {
    assert.match(app, new RegExp(`\\${extension}`))
  }
})
