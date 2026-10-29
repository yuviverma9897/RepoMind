import { useState } from 'react'
import { securityScan } from '../api'
import FindingsList, { SeverityBadge } from './FindingsList'
import { SourcesPanel } from './GroundedAnswer'

const ctl = 'rounded-md border border-slate-700 bg-slate-950/70 px-2 py-1.5 text-sm text-slate-100 focus:border-cyan-500/60 focus:outline-none'

export default function SecurityPanel({ repositoryId, onOpenSource }) {
  const [prefix, setPrefix] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)

  async function run(useAi) {
    setBusy(useAi ? 'ai' : 'scan')
    setError(null)
    try {
      setResult(await securityScan(repositoryId, { path_prefix: prefix.trim() || null, use_ai: useAi }))
    } catch (err) {
      setError(err.retryAfterSeconds ? `${err.message} Try again in about ${Math.ceil(err.retryAfterSeconds)} seconds.` : err.message)
    } finally {
      setBusy(null)
    }
  }

  const secretsOnly = Object.entries(result?.secrets_only_languages ?? {})
  return (
    <section className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4" data-testid="security-panel">
      <div>
        <h3 className="text-sm font-semibold text-slate-200">Security Analysis</h3>
        <p className="mt-0.5 text-sm text-slate-500">
          Pattern checks for secrets, command execution, injection, unsafe input, auth and exposed configuration, with an
          optional AI review. Results are <span className="text-slate-300">potential findings that need human review</span> —
          not proof of a vulnerability, and not a complete audit. Detected secrets are always masked.
        </p>
      </div>
      <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); run(false) }}>
        <input aria-label="Security path prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)}
          placeholder="limit to path prefix, e.g. src/ (optional)" className={`${ctl} min-w-0 flex-1 font-mono`} />
        <button type="submit" disabled={!!busy} className="rounded-lg border border-slate-700 px-4 py-1.5 text-sm text-slate-200 hover:border-cyan-500/50 disabled:opacity-60">
          {busy === 'scan' ? 'Scanning…' : 'Run scan'}
        </button>
        <button type="button" disabled={!!busy} onClick={() => run(true)} className="rounded-lg bg-cyan-500 px-4 py-1.5 text-sm font-semibold text-slate-950 hover:bg-cyan-400 disabled:opacity-60"
          title="Runs the scan, then one AI call reviews the top findings and auth/input-handling code">
          {busy === 'ai' ? 'Reviewing…' : 'Scan + AI review'}
        </button>
      </form>
      {error && <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{error}</p>}

      {result && (
        <div className="space-y-4 border-t border-slate-800 pt-4" data-testid="security-result">
          <div className="flex flex-wrap items-center gap-3 text-sm text-slate-300">
            <span>{result.total_findings} potential finding{result.total_findings === 1 ? '' : 's'} in {result.files_scanned} files</span>
            {['critical', 'high', 'medium', 'low', 'info'].map((s) =>
              result.counts.by_severity[s] ? (
                <span key={s} className="flex items-center gap-1"><SeverityBadge severity={s} /><span className="font-mono text-xs">{result.counts.by_severity[s]}</span></span>
              ) : null,
            )}
            <span className="font-mono text-xs text-slate-600">{(result.took_ms / 1000).toFixed(1)}s</span>
          </div>
          {result.truncated && <p className="text-xs text-amber-300">Showing the {result.findings.length} most severe findings. Use a path prefix to narrow the scan.</p>}
          {secretsOnly.length > 0 && (
            <p className="text-xs text-amber-300" data-testid="secrets-only-languages">
              Secret detection only (no code rules): {secretsOnly.map(([l, n]) => `${l} (${n} files)`).join(', ')}.
            </p>
          )}
          {result.ai.status !== 'not_requested' && (
            <p className="rounded-lg border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-xs text-violet-200" data-testid="security-ai">
              {result.ai.status === 'ok'
                ? `AI review (${result.ai.model}): ${result.ai.reviewed} findings reviewed, ${result.ai.new_findings} new potential findings` +
                  (result.ai.rejected ? `, ${result.ai.rejected} AI suggestions dropped because they did not point at the provided code` : '') + '.'
                : 'The AI response could not be parsed; only rule-based findings are shown.'}
            </p>
          )}
          <FindingsList findings={result.findings} onOpenSource={onOpenSource} emptyText="No potential issues matched the rules. This does not mean the code is secure." />
          {result.ai.sources?.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">Code shown to the AI reviewer ({result.ai.sources.length} excerpts, secrets masked)</summary>
              <div className="mt-2"><SourcesPanel sources={result.ai.sources} onOpenSource={onOpenSource} title="Reviewed code" /></div>
            </details>
          )}
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">Limitations</summary>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">{result.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
          </details>
        </div>
      )}
    </section>
  )
}
