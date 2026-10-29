import { useEffect, useState } from 'react'
import { generateEmbeddings, getEmbeddingStatus, searchRepository } from '../api'

const EXAMPLES = ['Where is authentication handled?', 'Where is the database connection created?', 'How is error handling performed?']
const TOP_K_OPTIONS = [5, 8, 15, 25]
const PREVIEW_LINES = 12

function SimilarityMeter({ value }) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div className="flex items-center gap-2" title="Cosine similarity between the question and this chunk (1 = identical direction)">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-800">
        <div className="h-full rounded-full bg-cyan-400" style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs text-slate-300">{value.toFixed(3)}</span>
    </div>
  )
}

function ResultCard({ result, onOpen }) {
  const [expanded, setExpanded] = useState(false)
  const lines = result.content.split('\n')
  const shown = expanded ? lines : lines.slice(0, PREVIEW_LINES)
  const gutter = shown.map((_, i) => result.start_line + i).join('\n')
  const symbol = result.symbol_name
    ? `${result.chunk_type}: ${result.parent_symbol ? `${result.parent_symbol}.` : ''}${result.symbol_name}`
    : result.chunk_type

  return (
    <article className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950/50">
      <header className="flex flex-col gap-2 border-b border-slate-800 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-slate-600">#{result.rank}</span>
            <button
              type="button"
              onClick={() => onOpen(result)}
              className="truncate text-left font-mono text-sm font-semibold text-cyan-300 hover:text-cyan-200 hover:underline"
              title={`Open ${result.file_path} at line ${result.start_line}`}
            >
              {result.file_path}
            </button>
            <span className="shrink-0 rounded border border-violet-500/30 bg-violet-500/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-violet-300">
              {result.language}
            </span>
          </div>
          <p className="mt-1 font-mono text-xs text-slate-400">
            {symbol}
            <span className="text-slate-600"> · </span>
            Lines {result.start_line}–{result.end_line}
            {result.part && <span className="text-slate-500"> · part {result.part}/{result.total_parts}</span>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-4">
          <SimilarityMeter value={result.similarity} />
          <button type="button" onClick={() => onOpen(result)} className="text-xs text-slate-400 hover:text-cyan-300">
            Open in viewer →
          </button>
        </div>
      </header>
      <div className="overflow-x-auto">
        <div className="flex min-w-max font-mono text-[12.5px] leading-5">
          <pre aria-hidden="true" className="select-none border-r border-slate-800 px-3 py-2 text-right text-slate-600">{gutter}</pre>
          <pre className="px-3 py-2 text-slate-200 [tab-size:4]"><code>{shown.join('\n')}</code></pre>
        </div>
      </div>
      {lines.length > PREVIEW_LINES && (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="w-full border-t border-slate-800 py-1.5 text-xs text-slate-500 hover:text-cyan-300"
        >
          {expanded ? 'Show less' : `Show all ${lines.length} lines`}
        </button>
      )}
    </article>
  )
}

export default function SearchPanel({ repositoryId, onOpenResult }) {
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(8)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [response, setResponse] = useState(null)
  const [embStatus, setEmbStatus] = useState(null)
  const [generating, setGenerating] = useState(false)

  useEffect(() => {
    let cancelled = false
    getEmbeddingStatus(repositoryId)
      .then((data) => !cancelled && setEmbStatus(data))
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [repositoryId])

  async function handleGenerateEmbeddings() {
    setGenerating(true)
    setError(null)
    try {
      const res = await generateEmbeddings(repositoryId)
      setEmbStatus(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setGenerating(false)
    }
  }

  async function run(text = query) {
    const q = text.trim()
    if (!q) {
      setError('Enter a search query.')
      return
    }
    setQuery(text)
    setLoading(true)
    setError(null)
    try {
      setResponse(await searchRepository(repositoryId, q, topK))
    } catch (err) {
      setResponse(null)
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const isEmbeddingsComplete = embStatus && embStatus.total_chunks > 0 && embStatus.remaining_chunks === 0
  const isEmbeddingsPartial = embStatus && embStatus.embedded_chunks > 0 && embStatus.remaining_chunks > 0

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60">
      <div className="space-y-3 px-5 py-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-slate-200">Semantic Code Search</h3>
              {isEmbeddingsComplete && (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 font-mono text-[11px] text-emerald-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  Vector Search Ready ({embStatus.embedded_chunks} chunks)
                </span>
              )}
              {isEmbeddingsPartial && (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2.5 py-0.5 font-mono text-[11px] text-cyan-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-pulse" />
                  {embStatus.embedded_chunks}/{embStatus.total_chunks} embedded ({embStatus.progress_percent}%)
                </span>
              )}
            </div>
            <p className="mt-0.5 text-sm text-slate-500">
              Find code by meaning using Google Gemini 768-dimensional embeddings and cosine similarity.
            </p>
          </div>
          {embStatus && !isEmbeddingsComplete && (
            <button
              type="button"
              onClick={handleGenerateEmbeddings}
              disabled={generating}
              className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-500/50 bg-cyan-500/10 px-3 py-1.5 text-xs font-medium text-cyan-300 hover:bg-cyan-500/20 disabled:opacity-60"
            >
              {generating && <span className="h-3 w-3 animate-spin rounded-full border-2 border-cyan-400/30 border-t-cyan-400" />}
              {generating ? 'Generating Embeddings…' : isEmbeddingsPartial ? 'Resume Embeddings' : 'Generate Embeddings'}
            </button>
          )}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            run()
          }}
          className="flex flex-col gap-2 sm:flex-row"
        >
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={1000}
            placeholder="Search this repository… e.g. Where is authentication handled?"
            aria-label="Search query"
            className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950/70 px-4 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-cyan-500/60 focus:outline-none"
          />
          <select
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
            aria-label="Number of results"
            className="rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm text-slate-300 focus:outline-none"
          >
            {TOP_K_OPTIONS.map((k) => (
              <option key={k} value={k}>Top {k}</option>
            ))}
          </select>
          <button
            type="submit"
            disabled={loading}
            className="flex items-center justify-center gap-2 rounded-lg bg-cyan-500 px-5 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading && <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-950/30 border-t-slate-950" />}
            {loading ? 'Searching…' : 'Search'}
          </button>
        </form>
        {!response && !error && (
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => run(example)}
                disabled={loading}
                className="rounded-md border border-slate-800 bg-slate-950/50 px-2.5 py-1 text-xs text-slate-400 transition hover:border-cyan-500/40 hover:text-cyan-300"
              >
                {example}
              </button>
            ))}
          </div>
        )}
      </div>

      {(error || response) && (
        <div className="space-y-3 border-t border-slate-800 px-5 py-4">
          {error && (
            <div role="alert" className="flex flex-col gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300 sm:flex-row sm:items-center sm:justify-between">
              <span>{error}</span>
              {error.toLowerCase().includes('embedding') && (
                <button
                  type="button"
                  onClick={handleGenerateEmbeddings}
                  disabled={generating}
                  className="shrink-0 rounded-lg bg-cyan-500 px-3 py-1 text-xs font-semibold text-slate-950 transition hover:bg-cyan-400"
                >
                  {generating ? 'Generating Embeddings…' : 'Generate Embeddings Now'}
                </button>
              )}
            </div>
          )}
          {response && (
            <>
              <p className="font-mono text-xs text-slate-500">
                {response.results.length} results · {response.searched_chunks.toLocaleString()} chunks searched ·{' '}
                {response.took_ms} ms · {response.model} ({response.metric})
              </p>
              {response.message && (
                <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
                  {response.message}
                </p>
              )}
              {response.results.map((result) => (
                <ResultCard key={result.chunk_id} result={result} onOpen={onOpenResult} />
              ))}
            </>
          )}
        </div>
      )}
    </section>
  )
}
