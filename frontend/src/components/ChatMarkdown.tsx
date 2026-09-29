import ReactMarkdown, { type Components } from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import type { SourceRef } from '../types'

interface ChatMarkdownProps {
  text: string
  citations: SourceRef[]
  onOpenSource: (source: SourceRef) => Promise<void>
}

interface MarkdownNode {
  type: string
  value?: string
  url?: string
  children?: MarkdownNode[]
}

function citationNodes(value: string): MarkdownNode[] {
  const nodes: MarkdownNode[] = []
  const marker = /〔(\d+)〕/g
  let last = 0
  let match: RegExpExecArray | null
  while ((match = marker.exec(value)) !== null) {
    if (match.index > last) nodes.push({ type: 'text', value: value.slice(last, match.index) })
    const number = Number(match[1])
    if (Number.isInteger(number) && number > 0) {
      nodes.push({ type: 'link', url: `#citation-${number}`, children: [{ type: 'text', value: `источник ${number}` }] })
    } else {
      nodes.push({ type: 'text', value: match[0] })
    }
    last = match.index + match[0].length
  }
  if (last < value.length) nodes.push({ type: 'text', value: value.slice(last) })
  return nodes.length ? nodes : [{ type: 'text', value }]
}

/** Convert citation markers into links after Markdown parsing has identified code blocks. */
function remarkCitations() {
  return (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode) => {
      if (node.type === 'code' || node.type === 'inlineCode' || !node.children) return
      const children: MarkdownNode[] = []
      node.children.forEach((child) => {
        if (child.type === 'text' && typeof child.value === 'string') {
          children.push(...citationNodes(child.value))
        } else {
          visit(child)
          children.push(child)
        }
      })
      node.children = children
    }
    visit(tree)
  }
}

function isExternalUrl(value: string): boolean {
  try {
    const url = new URL(value, window.location.href)
    return url.protocol === 'http:' || url.protocol === 'https:' || url.protocol === 'mailto:'
  } catch {
    return false
  }
}

export function ChatMarkdown({ text, citations, onOpenSource }: ChatMarkdownProps) {
  const components: Components = {
    a: ({ href, children }) => {
      const match = typeof href === 'string' ? /^#citation-(\d+)$/.exec(href) : null
      if (match) {
        const index = Number(match[1]) - 1
        const source = citations[index]
        if (!source) return <span className="inline-citation inline-citation-missing">источник {match[1]}</span>
        return <button type="button" className="inline-citation" onClick={() => void onOpenSource(source)} title={source.text}>{children}</button>
      }
      if (!href) return <span>{children}</span>
      return <a href={href} target={isExternalUrl(href) ? '_blank' : undefined} rel={isExternalUrl(href) ? 'noreferrer' : undefined}>{children}</a>
    },
    table: ({ children }) => <div className="message-markdown-table"><table>{children}</table></div>,
  }

  return <div className="message-markdown">
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkCitations]} rehypePlugins={[rehypeSanitize]} skipHtml components={components}>{text}</ReactMarkdown>
  </div>
}
