import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const styles = await readFile(resolve(root, 'src/styles.css'), 'utf8')

function cssBlock(selector) {
  const start = styles.indexOf(selector)
  assert.notEqual(start, -1, `Missing CSS selector: ${selector}`)
  const end = styles.indexOf('}', start)
  assert.notEqual(end, -1, `Unclosed CSS selector: ${selector}`)
  return styles.slice(start, end + 1)
}

test('motion tokens define short purposeful timings and non-linear curves', () => {
  assert.match(styles, /--duration-press:\s*140ms/)
  assert.match(styles, /--duration-state:\s*180ms/)
  assert.match(styles, /--duration-overlay:\s*220ms/)
  assert.match(styles, /--ease-out:\s*cubic-bezier\(0\.23, 1, 0\.32, 1\)/)
  assert.match(styles, /--ease-drawer:\s*cubic-bezier\(0\.32, 0\.72, 0, 1\)/)
  assert.doesNotMatch(styles, /transition\s*:\s*all\b/)
})

test('pointer hover lift is gated while press feedback remains available', () => {
  const pointerBlock = styles.slice(styles.indexOf('@media (hover: hover) and (pointer: fine)'))
  assert.match(pointerBlock, /\.button:hover[^}]*transform: translateY\(-1px\)/)
  assert.match(pointerBlock, /\.citation-chip:hover[^}]*transform: translateY\(-1px\)/)
  assert.match(styles, /\.button:active[^}]*transform: scale\(\.97\)/)
})

test('mobile drawers animate spatially and gate interaction while hidden', () => {
  const library = cssBlock('  .library { position: fixed;')
  const chat = cssBlock('  .app-shell .chat-panel, .app-shell.chat-full .chat-panel { position: fixed;')
  assert.match(library, /transform: translateX\(-102%\)/)
  assert.match(library, /visibility: hidden/)
  assert.match(library, /pointer-events: none/)
  assert.match(styles, /\.mobile-library-open \.library \{ transform: translateX\(0\); opacity: 1; visibility: visible; pointer-events: auto;/)
  assert.match(chat, /transform: translateX\(102%\)/)
  assert.match(chat, /visibility: hidden/)
  assert.match(chat, /pointer-events: none/)
  assert.match(styles, /\.app-shell\.mobile-chat-open \.chat-panel, \.app-shell\.chat-full \.chat-panel \{ opacity: 1; visibility: visible; pointer-events: auto; transform: translateX\(0\);/)
})

test('reduced motion removes movement but keeps low-frequency state feedback', () => {
  const reduced = styles.slice(styles.indexOf('@media (prefers-reduced-motion: reduce)'))
  assert.match(reduced, /html \{ scroll-behavior: auto !important; \}/)
  assert.match(reduced, /\.spin \{ animation: reduced-pulse 1\.4s ease-in-out infinite; \}/)
  assert.match(reduced, /\.thinking-mark span \{ animation: reduced-think 1\.4s ease-in-out infinite; \}/)
  assert.match(reduced, /\.modal-card, \.auth-detail-option-list[^}]*transition-property: opacity; transform: none;/)
  assert.match(reduced, /\.library, \.chat-panel \{ transition: opacity var\(--duration-state\)/)
  assert.doesNotMatch(reduced, /animation-duration:\s*\.01ms/)
})
