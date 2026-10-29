// Minimal, safe renderer for LLM answers: builds React elements (never raw HTML).
// Supports paragraphs, "- "/"1. " lists, #-headings, fenced code, `inline code`, **bold**,
// and source references like [S1], which are passed to renderCitation.

const FENCE = /^(`{3,})(\S*)\s*$/

function parseBlocks(text) {
  const blocks = []
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  let i = 0
  while (i < lines.length) {
    const fence = lines[i].match(FENCE)
    if (fence) {
      const code = []
      i++
      while (i < lines.length && !lines[i].startsWith(fence[1])) code.push(lines[i++])
      i++ // closing fence (or end of text)
      blocks.push({ type: 'code', lang: fence[2], text: code.join('\n') })
    } else if (/^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
      const ordered = /^\s*\d+\./.test(lines[i])
      const items = []
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ''))
        i++
      }
      blocks.push({ type: 'list', ordered, items })
    } else if (/^#{1,4}\s+/.test(lines[i])) {
      blocks.push({ type: 'heading', text: lines[i].replace(/^#{1,4}\s+/, '') })
      i++
    } else if (lines[i].trim() === '') {
      i++
    } else {
      const para = []
      while (i < lines.length && lines[i].trim() !== '' && !FENCE.test(lines[i]) && !/^\s*([-*]|\d+\.)\s+/.test(lines[i]) && !/^#{1,4}\s+/.test(lines[i])) {
        para.push(lines[i++])
      }
      blocks.push({ type: 'paragraph', text: para.join(' ') })
    }
  }
  return blocks
}

function Inline({ text, renderCitation }) {
  // Split on `code`, **bold** and [S12] (or [S1][S2]) while keeping the delimiters.
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[S\d+\])/g)
  return parts.map((part, i) => {
    if (/^`[^`]+`$/.test(part)) {
      return <code key={i} className="rounded bg-slate-800/80 px-1 py-0.5 font-mono text-[0.85em] text-cyan-200">{part.slice(1, -1)}</code>
    }
    if (/^\*\*[^*]+\*\*$/.test(part)) return <strong key={i} className="font-semibold text-slate-100">{part.slice(2, -2)}</strong>
    if (/^\[S\d+\]$/.test(part)) return <span key={i}>{renderCitation ? renderCitation(part.slice(1, -1)) : part}</span>
    return part
  })
}

export default function Markdown({ text, renderCitation }) {
  return (
    <div className="space-y-3 text-sm leading-relaxed text-slate-300">
      {parseBlocks(text).map((block, i) => {
        if (block.type === 'code') {
          return (
            <pre key={i} className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2 font-mono text-[12.5px] leading-5 text-slate-200">
              <code>{block.text}</code>
            </pre>
          )
        }
        if (block.type === 'list') {
          const List = block.ordered ? 'ol' : 'ul'
          return (
            <List key={i} className={`space-y-1 pl-5 ${block.ordered ? 'list-decimal' : 'list-disc'} marker:text-slate-600`}>
              {block.items.map((item, j) => (
                <li key={j}><Inline text={item} renderCitation={renderCitation} /></li>
              ))}
            </List>
          )
        }
        if (block.type === 'heading') {
          return <h4 key={i} className="pt-1 text-sm font-semibold text-slate-100"><Inline text={block.text} renderCitation={renderCitation} /></h4>
        }
        return <p key={i}><Inline text={block.text} renderCitation={renderCitation} /></p>
      })}
    </div>
  )
}
