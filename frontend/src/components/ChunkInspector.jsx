import { useRef, useState } from 'react'
import { analyzeCodeStructure } from '../api'

const PAGE_SIZE = 20

function Chips({ title, data }) {
  const entries = Object.entries(data || {})
  if (entries.length === 0) return null
  return (
    <div>
      <h4 className="mb-2 font-mono text-[11px] uppercase tracking-wider text-slate-500">{title}</h4>
      <div className="flex flex-wrap gap-2">
        {entries.map(([key, count]) => (
          <span key={key} className="rounded-md border border-slate-700/80 bg-slate-950/60 px-2 py-1 font-mono text-xs text-slate-300">
            {key} <span className="text-slate-500">{count.toLocaleString()}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

function Stat({ label, value, hint }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/50 px-4 py-3">
      <dt className="font-mono text-[11px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className="mt-1 truncate text-lg font-semibold text-slate-100" title={hint}>{value}</dd>
    </div>
  )
}

function ChunkCard({ chunk, onOpenFile }) {
  const lineCount = chunk.end_line - chunk.start_line + 1
  const contentLines = chunk.content.split('\n').length
  // Pieces of one very long line all share the same line number.
  const gutter = Array.from({ length: contentLines }, (_, i) =>
    lineCount === 1 ? chunk.start_line : chunk.start_line + i,
  ).join('\n')
  const symbol = chunk.symbol_name
    ? (chunk.parent_symbol ? `${chunk.parent_symbol}.` : '') + chunk.symbol_name
    : null

  return (
    <article className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950/40">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 rounded border border-cyan-500/30 bg-cyan-500/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-cyan-300">
            {chunk.chunk_type}
          </span>
          {symbol && <span className="truncate font-mono text-sm text-slate-100">{symbol}</span>}
          {chunk.part && (
            <span className="shrink-0 font-mono text-xs text-amber-300/80">
              part {chunk.part}/{chunk.total_parts}
            </span>
          )}
        </div>
        <div className="flex min-w-0 items-center gap-3 font-mono text-xs text-slate-500">
          <span className="truncate" title={chunk.file_path}>
            {chunk.file_path}:{chunk.start_line}–{chunk.end_line}
          </span>
          <span className="shrink-0">{chunk.content.length.toLocaleString()} chars</span>
          <button type="button" onClick={() => onOpenFile(chunk.file_path)} className="shrink-0 text-cyan-400 hover:text-cyan-300">
            Open file
          </button>
        </div>
      </header>
      <div className="max-h-72 overflow-auto">
        <div className="flex min-w-max font-mono text-xs leading-5">
          <pre aria-hidden="true" className="sticky left-0 select-none border-r border-slate-800 bg-slate-950 px-3 py-2 text-right text-slate-600">
            {gutter}
          </pre>
          <pre className="px-3 py-2 text-slate-300 [tab-size:4]"><code>{chunk.content}</code></pre>
        </div>
      </div>
    </article>
  )
}

export default function ChunkInspector({ repository, onOpenFile }) {
  const [status, setStatus] = useState('idle') // idle | loading | done | error
  const [error, setError] = useState(null)
  const [summary, setSummary] = useState(null)
  const [chunks, setChunks] = useState([])
  const [matched, setMatched] = useState(0)
  const [filePath, setFilePath] = useState('')
  const [pageLoading, setPageLoading] = useState(false)
  const latestRequest = useRef(0)

  const target = { owner: repository.owner, repo: repository.name, branch: repository.default_branch }

  async function analyze() {
    const id = ++latestRequest.current
    setStatus('loading')
    setError(null)
    try {
      const data = await analyzeCodeStructure({ ...target, limit: PAGE_SIZE })
      if (id !== latestRequest.current) return
      setSummary(data)
      setChunks(data.chunks)
      setMatched(data.matched_chunks)
      setFilePath('')
      setStatus('done')
    } catch (err) {
      if (id !== latestRequest.current) return
      setError(err.message)
      setStatus('error')
    }
  }

  async function loadChunks(path, offset) {
    const id = ++latestRequest.current
    setPageLoading(true)
    setError(null)
    try {
      const data = await analyzeCodeStructure({
        ...target, offset, limit: PAGE_SIZE, filePath: path || null, includeFiles: false,
      })
      if (id !== latestRequest.current) return
      setChunks((previous) => (offset === 0 ? data.chunks : [...previous, ...data.chunks]))
      setMatched(data.matched_chunks)
    } catch (err) {
      if (id === latestRequest.current) setError(err.message)
    } finally {
      if (id === latestRequest.current) setPageLoading(false)
    }
  }

  function changeFile(path) {
    setFilePath(path)
    setChunks([])
    loadChunks(path, 0)
  }

  const skippedTotal = summary ? Object.values(summary.skipped_files).reduce((a, b) => a + b, 0) : 0

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60">
      <div className="flex flex-col gap-3 border-b border-slate-800 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-200">Code Structure</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            Developer view: parses supported files and splits them into chunks, the units RepoMind will later embed for search.
          </p>
        </div>
        <button
          type="button"
          onClick={analyze}
          disabled={status === 'loading'}
          className="flex shrink-0 items-center justify-center gap-2 rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-4 py-2 text-sm font-semibold text-cyan-300 transition hover:bg-cyan-500/20 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {status === 'loading' && (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-cyan-300/30 border-t-cyan-300" />
          )}
          {status === 'loading' ? 'Analyzing…' : summary ? 'Re-analyze' : 'Analyze Code Structure'}
        </button>
      </div>

      <div className="space-y-6 p-5">
        {status === 'idle' && (
          <p className="text-sm text-slate-500">
            Downloads the repository once, filters out non-source files, detects functions, classes and other
            structures, and shows the resulting chunks.
          </p>
        )}
        {status === 'loading' && <p className="text-sm text-slate-400">Downloading and analyzing the repository…</p>}
        {error && (
          <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
            {error}
          </p>
        )}

        {summary && status !== 'loading' && (
          <>
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-5">
              <Stat label="Files processed" value={`${summary.processed_files.toLocaleString()} / ${summary.total_files.toLocaleString()}`} />
              <Stat label="Chunks" value={summary.total_chunks.toLocaleString()} />
              <Stat label="Files skipped" value={skippedTotal.toLocaleString()} />
              <Stat label="Chunk size" value={`${summary.chunk_size} / ${summary.chunk_overlap}`} hint="CHUNK_SIZE / CHUNK_OVERLAP (characters)" />
              <Stat label="Commit" value={summary.commit_sha ? summary.commit_sha.slice(0, 7) : '—'} hint={summary.commit_sha || ''} />
            </dl>

            {summary.truncated && (
              <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-300">
                Large repository: only the first {summary.processed_files.toLocaleString()} eligible files were processed
                (source code is prioritised over tests and docs).
              </p>
            )}

            <div className="grid gap-5 md:grid-cols-2">
              <Chips title="Languages (files)" data={summary.languages} />
              <Chips title="Chunk types" data={summary.chunk_types} />
              <Chips title="Parsers (files)" data={summary.parsers} />
              <Chips title="Skipped (reason)" data={summary.skipped_files} />
            </div>

            <div className="space-y-3">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <select
                  value={filePath}
                  onChange={(e) => changeFile(e.target.value)}
                  aria-label="Filter chunks by file"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-300 focus:border-cyan-500/50 focus:outline-none sm:max-w-xl"
                >
                  <option value="">All files ({summary.total_chunks.toLocaleString()} chunks)</option>
                  {summary.files.map((f) => (
                    <option key={f.path} value={f.path}>
                      {f.path} ({f.chunk_count}) · {f.parser}
                    </option>
                  ))}
                </select>
                <span className="font-mono text-xs text-slate-500">
                  Showing {chunks.length.toLocaleString()} of {matched.toLocaleString()}
                </span>
              </div>

              {chunks.map((chunk) => (
                <ChunkCard key={chunk.chunk_id} chunk={chunk} onOpenFile={onOpenFile} />
              ))}

              {pageLoading && <p className="text-center text-sm text-slate-500">Loading chunks…</p>}
              {!pageLoading && chunks.length < matched && (
                <button
                  type="button"
                  onClick={() => loadChunks(filePath, chunks.length)}
                  className="w-full rounded-lg border border-slate-700 py-2 text-sm text-slate-300 transition hover:border-cyan-500/50 hover:text-cyan-300"
                >
                  Load {Math.min(PAGE_SIZE, matched - chunks.length)} more
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  )
}
