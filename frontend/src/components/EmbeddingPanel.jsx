import { useEffect, useRef, useState } from 'react'
import { generateEmbeddings, getEmbeddingStatus } from '../api'

const MAX_AUTO_WAITS = 5 // consecutive rate-limit waits without progress before giving up

function Spinner() {
  return <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-cyan-400/30 border-t-cyan-300 align-[-2px]" />
}

function ProgressBar({ percent }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
      <div className="h-full rounded-full bg-cyan-400 transition-all duration-500" style={{ width: `${percent}%` }} />
    </div>
  )
}

export default function EmbeddingPanel({ repositoryId, indexedAt }) {
  const [status, setStatus] = useState(null)
  const [phase, setPhase] = useState('idle') // idle | running | waiting
  const [waitSeconds, setWaitSeconds] = useState(0)
  const [error, setError] = useState(null)
  const stopRef = useRef(false)

  // Re-read after every (re-)index: changed files leave new chunks to embed.
  useEffect(() => {
    let cancelled = false
    getEmbeddingStatus(repositoryId)
      .then((data) => !cancelled && setStatus(data))
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [repositoryId, indexedAt])

  useEffect(() => () => (stopRef.current = true), []) // stop the loop when leaving the page

  async function waitFor(seconds) {
    setPhase('waiting')
    for (let left = Math.ceil(seconds); left > 0 && !stopRef.current; left--) {
      setWaitSeconds(left)
      await new Promise((resolve) => setTimeout(resolve, 1000))
    }
    setPhase('running')
  }

  async function generate() {
    stopRef.current = false
    setError(null)
    setPhase('running')
    let waits = 0
    while (!stopRef.current) {
      try {
        const run = await generateEmbeddings(repositoryId)
        setStatus(run)
        if (run.newly_embedded > 0) waits = 0
        if (run.status === 'complete') break
        if (run.status === 'interrupted') {
          if (run.error?.retry_after_seconds != null && waits++ < MAX_AUTO_WAITS) {
            await waitFor(run.error.retry_after_seconds)
            continue
          }
          setError(run.error?.message || 'Embedding was interrupted.')
          break
        }
        // in_progress: the request's time budget was used up, continue with the next call
      } catch (err) {
        if (err.code === 'embedding_rate_limited' && err.retryAfterSeconds != null && waits++ < MAX_AUTO_WAITS) {
          await waitFor(err.retryAfterSeconds)
          continue
        }
        setError(err.message)
        getEmbeddingStatus(repositoryId).then(setStatus).catch(() => {})
        break
      }
    }
    setPhase('idle')
  }

  const busy = phase !== 'idle'
  const total = status?.total_chunks ?? 0
  const done = status ? status.embedded_chunks + status.skipped_chunks : 0
  const complete = status && total > 0 && status.remaining_chunks === 0
  const started = done > 0 && !complete

  let line
  if (!status) line = <span className="text-slate-500">{error ? '' : 'Checking embedding status…'}</span>
  else if (phase === 'waiting')
    line = <span className="text-amber-300">Rate limited by the provider. Continuing in {waitSeconds}s… ({done.toLocaleString()} / {total.toLocaleString()})</span>
  else if (busy)
    line = (
      <span className="text-slate-300">
        <Spinner /> <span className="ml-1">Generating embeddings… {done.toLocaleString()} / {total.toLocaleString()}</span>
      </span>
    )
  else if (total === 0) line = <span className="text-slate-400">No code chunks to embed.</span>
  else if (complete)
    line = (
      <span className="text-slate-300">
        <span className="mr-2 inline-block h-2 w-2 rounded-full bg-emerald-400 align-middle" />
        <span className="text-emerald-300">Embeddings ready</span> · {done.toLocaleString()} / {total.toLocaleString()} chunks embedded
      </span>
    )
  else if (started)
    line = <span className="text-slate-300">{done.toLocaleString()} / {total.toLocaleString()} chunks embedded · {status.remaining_chunks.toLocaleString()} remaining</span>
  else line = <span className="text-slate-400">Code chunks: {total.toLocaleString()} · not embedded yet</span>

  return (
    <div className="space-y-3 border-t border-slate-800 px-5 py-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-200">Code Embeddings</h3>
          <p className="mt-0.5 text-sm">{line}</p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {status && (
            <span className="font-mono text-xs text-slate-500">
              {status.model} · {status.dimensions} dims
            </span>
          )}
          {busy ? (
            <button
              type="button"
              onClick={() => (stopRef.current = true)}
              className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-slate-500"
            >
              Stop
            </button>
          ) : (
            status && total > 0 && !complete && (
              <button
                type="button"
                onClick={generate}
                className="rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400"
              >
                {started ? 'Resume Embeddings' : 'Generate Embeddings'}
              </button>
            )
          )}
        </div>
      </div>

      {status && total > 0 && (busy || started) && <ProgressBar percent={status.progress_percent} />}

      {status && !status.provider_configured && !complete && !error && (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          No embedding API key is configured on the backend. Set <code className="font-mono">GEMINI_API_KEY</code> in{' '}
          <code className="font-mono">backend/.env</code> and restart the backend.
        </p>
      )}
      {error && !busy && (
        <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
          {started && <span className="text-rose-300/80"> Progress so far is saved; press Resume to continue.</span>}
        </p>
      )}
      {status?.skipped_chunks > 0 && complete && (
        <p className="font-mono text-xs text-slate-500">{status.skipped_chunks} empty chunk(s) skipped (nothing to embed)</p>
      )}
    </div>
  )
}
