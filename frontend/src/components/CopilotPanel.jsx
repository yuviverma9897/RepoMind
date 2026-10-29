import { useEffect, useState } from 'react'
import { generateEmbeddings, getEmbeddingStatus, runAgent } from '../api'
import { AgentFlow, StatusBadge } from './AgentActivity'
import FindingsList from './FindingsList'
import GroundedAnswer from './GroundedAnswer'

const MAX_EXCHANGES = 10 // current session only; nothing is stored
const SUGGESTED_QUESTIONS = [
  'How does this project handle authentication?',
  'Explain the overall project architecture',
  'What are the core routes and page components?',
  'Where are API requests and services defined?',
]

function Exchange({ item, onOpenSource }) {
  const r = item.response
  const findings = r.metadata?.findings ?? []
  return (
    <article className="space-y-3 rounded-lg border border-slate-800 bg-slate-950/40 p-4" data-testid="copilot-exchange">
      <p className="text-sm font-medium text-slate-100">{r.query}</p>
      <div className="space-y-1.5 rounded-md border border-slate-800 bg-slate-900/50 px-3 py-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-mono uppercase tracking-wider text-slate-500">Agent</span>
          <span className="font-medium text-cyan-300" data-testid="agent-used">{r.agent_title}</span>
          <StatusBadge status={r.status} />
          {r.routing.fallback && <span className="text-slate-500">(fallback)</span>}
          {r.routing.ambiguous && <span className="text-amber-300/80">ambiguous request</span>}
        </div>
        <p className="text-xs text-slate-500">{r.routing.reason}</p>
        <AgentFlow steps={r.activity} />
      </div>
      {r.status === 'failed' || r.status === 'unsupported' || r.status === 'needs_input' ? (
        <p role={r.status === 'failed' ? 'alert' : undefined}
          className={`rounded-lg border px-4 py-3 text-sm ${r.status === 'failed' ? 'border-rose-500/30 bg-rose-500/10 text-rose-200' : 'border-slate-700 bg-slate-900 text-slate-300'}`}>
          {r.answer}
          {r.error?.retry_after_seconds ? ` Try again in about ${Math.ceil(r.error.retry_after_seconds)} seconds.` : ''}
        </p>
      ) : (
        <GroundedAnswer
          result={{ ...r, answer: r.answer, model: r.metadata?.model, usage: r.metadata?.usage, citations: r.metadata?.citations,
            evidence: r.metadata?.evidence, truncated: r.metadata?.truncated, message: r.metadata?.message }}
          onOpenSource={onOpenSource}
        />
      )}
      {findings.length > 0 && (
        <details className="rounded-lg border border-slate-800 px-3 py-2">
          <summary className="cursor-pointer text-xs text-slate-400">
            Potential findings ({findings.length}{r.metadata.total_findings > findings.length ? ` of ${r.metadata.total_findings}` : ''}) —
            open the {r.agent === 'security' ? 'Security' : 'Debug'} tab for filters and details
          </summary>
          <div className="mt-2"><FindingsList findings={findings} onOpenSource={onOpenSource} /></div>
        </details>
      )}
    </article>
  )
}

export default function CopilotPanel({ repositoryId, onOpenSource, onAgentRun }) {
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [exchanges, setExchanges] = useState([])
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

  async function ask(queryText = question) {
    const q = (queryText || '').trim()
    if (!q) {
      setError('Enter a question.')
      return
    }
    setQuestion(q)
    setLoading(true)
    setError(null)
    try {
      const response = await runAgent(repositoryId, q)
      setExchanges((prev) => [{ id: Date.now(), response }, ...prev].slice(0, MAX_EXCHANGES))
      onAgentRun?.(response)
      setQuestion('')
    } catch (err) {
      setError(
        err.retryAfterSeconds ? `${err.message} Try again in about ${Math.ceil(err.retryAfterSeconds)} seconds.` : err.message,
      )
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
              <h3 className="text-sm font-semibold text-slate-200">Repository Multi-Agent Copilot</h3>
              {isEmbeddingsComplete && (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 font-mono text-[11px] text-emerald-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  RAG Neural Search Ready ({embStatus.embedded_chunks} chunks)
                </span>
              )}
              {isEmbeddingsPartial && (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2.5 py-0.5 font-mono text-[11px] text-cyan-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-pulse" />
                  {embStatus.embedded_chunks}/{embStatus.total_chunks} embedded ({embStatus.progress_percent}%)
                </span>
              )}
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
                ✨ Plain English & Human-Friendly
              </span>
            </div>
            <p className="mt-0.5 text-sm text-slate-500">
              Ask any question in plain English. RepoMind gives simple, human-friendly, cited explanations without dense technical jargon.
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
            ask()
          }}
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
        >
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                ask()
              }
            }}
            rows={2}
            maxLength={1000}
            placeholder="Ask anything... e.g. How does this project handle authentication? · Explain get_db · What breaks if I change db.py? (Enter to ask)"
            aria-label="Question"
            className="min-w-0 flex-1 resize-y rounded-lg border border-slate-700 bg-slate-950/70 px-4 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-cyan-500/60 focus:outline-none"
          />
          <button
            type="submit"
            disabled={loading}
            className="flex items-center justify-center gap-2 rounded-lg bg-cyan-500 px-5 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading && <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-950/30 border-t-slate-950" />}
            {loading ? 'Thinking…' : 'Ask Copilot'}
          </button>
        </form>
        <div className="flex flex-wrap gap-2 pt-1">
          {SUGGESTED_QUESTIONS.map((sq) => (
            <button
              key={sq}
              type="button"
              onClick={() => ask(sq)}
              disabled={loading}
              className="rounded-md border border-slate-800 bg-slate-950/50 px-2.5 py-1 text-xs text-slate-400 transition hover:border-cyan-500/40 hover:text-cyan-300 disabled:opacity-50"
            >
              {sq}
            </button>
          ))}
        </div>
        {loading && <p className="text-xs text-cyan-400 animate-pulse">Routing request to specialist agent and synthesizing grounded answer…</p>}
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
                {generating ? 'Generating…' : 'Generate Embeddings Now'}
              </button>
            )}
          </div>
        )}
      </div>
      {exchanges.length > 0 && (
        <div className="space-y-3 border-t border-slate-800 px-5 py-4">
          {exchanges.map((item) => (
            <Exchange key={item.id} item={item} onOpenSource={onOpenSource} />
          ))}
        </div>
      )}
    </section>
  )
}
