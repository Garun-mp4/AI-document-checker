import type { PreviewBlock, SourceRef, StreamCitation } from '../types'

export type ViewerSource = SourceRef | StreamCitation | PreviewBlock
export type MatchQuality = 'exact' | 'approximate' | 'page_only' | 'not_found' | 'calculation'

export interface NormalizedRange {
  start: number
  end: number
}

export interface PdfOcrWordBox {
  key: number
  left: number
  top: number
  width: number
  height: number
}

interface HighlightRegistryLike { set(name: string, value: unknown): void; delete(name: string): boolean }
interface HighlightConstructorLike { new (...ranges: Range[]): unknown }

function getHighlightRegistry(): HighlightRegistryLike | undefined {
  return (globalThis.CSS as unknown as { highlights?: HighlightRegistryLike } | undefined)?.highlights
}

export function installTextHighlight(name: string, ranges: Range[]): boolean {
  const registry = getHighlightRegistry()
  const HighlightConstructor = (globalThis as typeof globalThis & { Highlight?: HighlightConstructorLike }).Highlight
  if (!registry || !HighlightConstructor || !ranges.length) return false
  registry.set(name, new HighlightConstructor(...ranges))
  return true
}

export function clearTextHighlight(name: string): void {
  getHighlightRegistry()?.delete(name)
}

export function supportsTextHighlights(): boolean {
  return Boolean(getHighlightRegistry() && (globalThis as typeof globalThis & { Highlight?: HighlightConstructorLike }).Highlight)
}

export function sourceQuery(source: ViewerSource | null): string {
  if (!source) return ''
  return source.text.replace(/\s+/g, ' ').trim()
}

export function normalizedRange(text: string, query: string, startHint = 0): NormalizedRange | null {
  const normalized: string[] = []
  const starts: number[] = []
  const ends: number[] = []
  let inWhitespace = false
  let offset = 0
  for (const character of text) {
    const transformed = character.normalize('NFKC').toLocaleLowerCase()
    if (/\s/u.test(character) || /\s/u.test(transformed)) {
      if (normalized.length && !inWhitespace) {
        normalized.push(' '); starts.push(offset); ends.push(offset + character.length)
      } else if (normalized.length && inWhitespace) ends[ends.length - 1] = offset + character.length
      inWhitespace = true
      offset += character.length
      continue
    }
    inWhitespace = false
    for (let index = 0; index < transformed.length; index += 1) {
      const item = transformed[index]
      normalized.push(item); starts.push(offset); ends.push(offset + character.length)
    }
    offset += character.length
  }
  const needle = query.normalize('NFKC').toLocaleLowerCase().trim().replace(/\s+/gu, ' ')
  if (!needle) return null
  const haystack = normalized.join('')
  let best: number | null = null
  let bestDistance = Number.POSITIVE_INFINITY
  let from = 0
  while (from <= haystack.length - needle.length) {
    const found = haystack.indexOf(needle, from)
    if (found < 0) break
    const distance = Math.abs(starts[found] - startHint)
    if (distance < bestDistance) { best = found; bestDistance = distance }
    from = found + 1
  }
  return best === null ? null : { start: starts[best], end: ends[best + needle.length - 1] }
}

export function isCalculation(source: ViewerSource | null): boolean {
  return Boolean(source && (
    ('is_derived' in source && source.is_derived) ||
    source.locator.source_type === 'calculation' || source.locator.derived === true
  ))
}

export function pdfOcrWordBoxes(source: ViewerSource | null): PdfOcrWordBox[] {
  const rawMap = source?.locator.ocr_map
  if (!rawMap || typeof rawMap !== 'object') return []
  const map = rawMap as Record<string, unknown>
  const scale = map.coordinate_scale
  const rawBoxes = map.word_boxes
  if (typeof scale !== 'number' || scale <= 0 || !Array.isArray(rawBoxes)) return []
  return rawBoxes.flatMap((raw, key) => {
    if (!Array.isArray(raw) || raw.length < 8 || !raw.slice(0, 8).every((value) => typeof value === 'number' && Number.isFinite(value))) return []
    const [x0, y0, x1, y1] = raw as number[]
    if (x1 <= x0 || y1 <= y0) return []
    return [{
      key,
      left: x0 / scale * 100,
      top: y0 / scale * 100,
      width: (x1 - x0) / scale * 100,
      height: (y1 - y0) / scale * 100,
    }]
  })
}

export function scrollIntoViewRespectingMotion(target: Element | null | undefined, block: ScrollLogicalPosition = 'center'): void {
  if (!target) return
  const reduceMotion = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block })
}

export function navigateToSource(target: Element | null | undefined): void {
  const viewer = document.getElementById('document-original-viewer')
  scrollIntoViewRespectingMotion(viewer, 'nearest')
  window.requestAnimationFrame(() => scrollIntoViewRespectingMotion(target, 'center'))
}
