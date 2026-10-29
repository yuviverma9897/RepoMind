import { useEffect, useState } from 'react'
import { analyzeAndIndex, getIndexStatus, storedChunksUrl } from '../api'
import EmbeddingPanel from './EmbeddingPanel'

function timeAgo(iso) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  return new Date(iso).toLocaleDateString()
}

function Stat({ label, value }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/50 px-4 py-3">
      <dt className="font-mono text-[11px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className="mt-1 text-lg font-semibold text-slate-100">{value}</dd>
    </div>
  )
}

function StatusLine({ status, statusError }) {
  if (statusError) return <span className="text-amber-300">{statusError}</span>
  if (!status) return <span className="text-slate-500">Checking index status…</span>
  if (!status.indexed) return <span className="text-slate-400">Not indexed yet</span>
  return (
    <span className="text-slate-300">
      <span className="mr-2 inline-block h-2 w-2 rounded-full bg-emerald-400 align-middle" />
      Indexed {timeAgo(status.indexed_at)} · {status.chunks_total?.toLocaleString()} chunks
      {status.commit_sha && <span className="font-mono text-slate-500"> · {status.commit_sha.slice(0, 7)}</span>}
    </span>
  )
}

export default function IndexPanel({ repository, onStatusChange }) {
  const [status, setStatus] = useState(null)
  const [statusError, setStatusError] = useState(null)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    getIndexStatus({ owner: repository.owner, repo: repository.name })
      .then((data) => !cancelled && setStatus(data))
      .catch((err) => !cancelled && setStatusError(err.message))
    return () => {
      cancelled = true
    }
  }, [repository.owner, repository.name])

  useEffect(() => {
    onStatusChange?.(status) // lets the dashboard enable search once the repository is indexed
  }, [status, onStatusChange])

  async function run() {
    setRunning(true)
    setError(null)
    setResult(null) // never show a previous run's result next to a new error
    try {
      const data = await analyzeAndIndex({
        owner: repository.owner,
        repo: repository.name,
        branch: repository.default_branch,
      })
      setResult(data)
      setStatusError(null)
      setStatus({
        indexed: true,
        repository_id: data.repository_id,
        indexed_at: data.indexed_at,
        commit_sha: data.commit_sha,
        chunks_total: data.chunks_total,
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60">
      <div className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-200">Repository Index</h3>
          <p className="mt-0.5 text-sm">
            <StatusLine status={status} statusError={statusError} />
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {status?.indexed && (
            <a
              href={storedChunksUrl(status.repository_id)}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-slate-400 hover:text-cyan-300"
            >
              Stored chunks (JSON)
            </a>
          )}
          <button
            type="button"
            onClick={run}
            disabled={running}
            className="flex items-center justify-center gap-2 rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {running && <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-950/30 border-t-slate-950" />}
            {running ? 'Indexing…' : status?.indexed ? 'Re-index Repository' : 'Analyze & Index Repository'}
          </button>
        </div>
      </div>

      {(running || error || result) && (
        <div className="space-y-4 border-t border-slate-800 px-5 py-4">
          {running && (
            <p className="text-sm text-slate-400">
              Downloading the repository, chunking source files and saving them to the database…
            </p>
          )}
          {error && !running && (
            <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
              {error}
            </p>
          )}
          {result && !running && (
            <>
              <p className="text-sm">
                {result.status === 'indexed' ? (
                  <span className="text-emerald-300">Repository indexed successfully.</span>
                ) : (
                  <span className="text-emerald-300">Index is already up to date. No changes were needed.</span>
                )}
                <span className="ml-2 font-mono text-xs text-slate-500">
                  {result.branch} · {(result.duration_ms / 1000).toFixed(1)}s
                </span>
              </p>
              <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Stat label="Files processed" value={result.files_processed.toLocaleString()} />
                <Stat label="Chunks created" value={result.chunks_created.toLocaleString()} />
                <Stat label="Chunks stored" value={result.chunks_total.toLocaleString()} />
                <Stat label="Files skipped" value={result.files_skipped.toLocaleString()} />
              </dl>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[11px] uppercase tracking-wider text-slate-500">Languages</span>
                {result.languages.map((lang) => (
                  <span key={lang} className="rounded-md border border-slate-700/80 bg-slate-950/60 px-2 py-1 font-mono text-xs text-slate-300">
                    {lang} <span className="text-slate-500">{result.language_files[lang]}</span>
                  </span>
                ))}
              </div>
              <p className="font-mono text-xs text-slate-500">
                Sync: {result.files_added} added · {result.files_updated} updated · {result.files_removed} removed ·{' '}
                {result.files_unchanged} unchanged · {result.chunks_deleted} chunks deleted
                {result.truncated && ' · file limit reached (source code prioritised)'}
              </p>
            </>
          )}
        </div>
      )}

      {status?.indexed && !running && (
        <EmbeddingPanel repositoryId={status.repository_id} indexedAt={status.indexed_at} />
      )}
    </section>
  )
}
