import { useState } from 'react'

// URL validation lives in the backend so there is a single source of truth.
export default function RepoUrlForm({ onAnalyze, loading, error }) {
  const [url, setUrl] = useState('')

  function handleSubmit(event) {
    event.preventDefault()
    const trimmed = url.trim()
    if (trimmed && !loading) onAnalyze(trimmed)
  }

  return (
    <form onSubmit={handleSubmit} className="w-full">
      <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/70 p-2 shadow-2xl shadow-cyan-950/30 sm:flex-row">
        <div className="flex flex-1 items-center gap-3 px-3">
          <svg viewBox="0 0 16 16" className="h-5 w-5 shrink-0 fill-slate-500" aria-hidden="true">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
          </svg>
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/owner/repository"
            aria-label="GitHub repository URL"
            spellCheck="false"
            disabled={loading}
            className="w-full bg-transparent py-3 font-mono text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none disabled:opacity-60"
          />
        </div>
        <button
          type="submit"
          className="flex items-center justify-center gap-2 rounded-lg bg-cyan-500 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!url.trim() || loading}
        >
          {loading && (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-950/30 border-t-slate-950" />
          )}
          {loading ? 'Analyzing…' : 'Analyze Repository'}
        </button>
      </div>

      {error && (
        <div
          role="alert"
          className="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-left text-sm text-rose-300"
        >
          {error}
        </div>
      )}
    </form>
  )
}
