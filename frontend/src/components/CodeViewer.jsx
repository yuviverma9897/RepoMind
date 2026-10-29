import { useEffect, useRef } from 'react'

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function Shell({ children, header }) {
  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60">
      {header}
      <div className="relative min-h-0 flex-1">{children}</div>
    </section>
  )
}

function Centered({ children }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center text-sm text-slate-500">
      {children}
    </div>
  )
}

function Header({ path, file }) {
  const name = path.split('/').pop()
  const lineCount = file?.content != null ? countLines(file.content) : null

  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-800 px-5 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <h3 className="truncate font-mono text-sm font-semibold text-slate-100">{name}</h3>
          {file && (
            <span className="shrink-0 rounded border border-violet-500/30 bg-violet-500/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-violet-300">
              {file.language}
            </span>
          )}
        </div>
        <p className="truncate font-mono text-xs text-slate-500" title={path}>{path}</p>
      </div>
      {file && (
        <div className="flex shrink-0 items-center gap-4 font-mono text-xs text-slate-500">
          {lineCount != null && <span>{lineCount.toLocaleString()} lines</span>}
          <span>{formatSize(file.size)}</span>
          {file.html_url && (
            <a
              href={file.html_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-slate-400 transition hover:text-cyan-300"
            >
              GitHub ↗
            </a>
          )}
        </div>
      )}
    </div>
  )
}

function countLines(content) {
  if (content === '') return 0
  const lines = content.split('\n').length
  return content.endsWith('\n') ? lines - 1 : lines
}

export default function CodeViewer({ selectedPath, file, loading, error, onRetry, highlight = null }) {
  if (!selectedPath) {
    return (
      <Shell>
        <Centered>
          <svg viewBox="0 0 24 24" className="h-10 w-10 stroke-slate-700" fill="none" strokeWidth="1.5" aria-hidden="true">
            <path d="M8 9l-3 3 3 3M16 9l3 3-3 3M13.5 6l-3 12" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <p>Select a file in the explorer to view its source code.</p>
        </Centered>
      </Shell>
    )
  }

  const header = <Header path={selectedPath} file={loading || error ? null : file} />

  if (loading) {
    return (
      <Shell header={header}>
        <Centered>
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-slate-700 border-t-cyan-400" />
          <p>Loading file…</p>
        </Centered>
      </Shell>
    )
  }

  if (error) {
    return (
      <Shell header={header}>
        <Centered>
          <p className="max-w-md rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-rose-300">{error}</p>
          <button
            type="button"
            onClick={onRetry}
            className="rounded-lg border border-slate-700 px-4 py-2 text-slate-300 transition hover:border-cyan-500/50 hover:text-cyan-300"
          >
            Try again
          </button>
        </Centered>
      </Shell>
    )
  }

  if (!file) return <Shell header={header} />

  // Binary or oversized files: the backend sends a message instead of content.
  if (file.kind !== 'text') {
    return (
      <Shell header={header}>
        <Centered>
          <p className="max-w-md rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-amber-300">
            {file.message}
          </p>
          {file.html_url && (
            <a href={file.html_url} target="_blank" rel="noopener noreferrer" className="text-cyan-400 hover:text-cyan-300">
              Open on GitHub ↗
            </a>
          )}
        </Centered>
      </Shell>
    )
  }

  if (file.content === '') {
    return (
      <Shell header={header}>
        <Centered><p>This file is empty.</p></Centered>
      </Shell>
    )
  }

  return (
    <Shell header={header}>
      {file.secrets_masked > 0 && (
        <p data-testid="secrets-masked" className="border-b border-amber-500/20 bg-amber-500/10 px-4 py-1.5 text-xs text-amber-200">
          {file.secrets_masked} possible secret{file.secrets_masked > 1 ? 's' : ''} masked with * (line numbers unchanged).
        </p>
      )}
      <CodeBody content={file.content} highlight={highlight} />
    </Shell>
  )
}

const LINE_HEIGHT = 24 // px, matches leading-6
const PADDING_TOP = 12 // px, matches py-3

// Two <pre> blocks (gutter + code) keep the DOM small even for files with thousands of lines.
// An optional highlight ({ start, end }) is a single overlay, scrolled into view.
function CodeBody({ content, highlight }) {
  const scrollRef = useRef(null)
  const lineCount = countLines(content)
  const gutter = Array.from({ length: lineCount }, (_, i) => i + 1).join('\n')
  const top = highlight ? PADDING_TOP + (highlight.start - 1) * LINE_HEIGHT : 0

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = highlight ? Math.max(0, top - 3 * LINE_HEIGHT) : 0
  }, [highlight, content, top])

  return (
    <div ref={scrollRef} className="absolute inset-0 overflow-auto">
      <div className="relative flex min-w-max font-mono text-[13px] leading-6">
        {highlight && (
          <div
            data-testid="code-highlight"
            className="pointer-events-none absolute inset-x-0 border-l-2 border-cyan-400 bg-cyan-400/10"
            style={{ top, height: (highlight.end - highlight.start + 1) * LINE_HEIGHT }}
          />
        )}
        <pre
          aria-hidden="true"
          className="sticky left-0 z-10 select-none border-r border-slate-800 bg-slate-900 px-4 py-3 text-right text-slate-600"
        >
          {gutter}
        </pre>
        <pre className="relative px-4 py-3 text-slate-200 [tab-size:4]">
          <code>{content}</code>
        </pre>
      </div>
    </div>
  )
}
