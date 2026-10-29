import { useEffect, useMemo, useState } from 'react'
import { debugInvestigate, getSymbols } from '../api'
import FindingsList from './FindingsList'
import Markdown from './Markdown'
import { CitationChip, EvidenceNotice, SourcesPanel } from './GroundedAnswer'

const ctl = 'rounded-md border border-slate-700 bg-slate-950/70 px-2 py-1.5 text-sm text-slate-100 focus:border-cyan-500/60 focus:outline-none'
const MAX = 1000

export default function DebugPanel({ repositoryId, files, selectedPath, target, onOpenSource }) {
  const [description, setDescription] = useState('')
  const [filePath, setFilePath] = useState('')
  const [symbols, setSymbols] = useState([])
  const [symbol, setSymbol] = useState('') // "parent.name" or "name"; '' = whole file
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const paths = useMemo(() => new Set(files.filter((f) => f.type === 'file').map((f) => f.path)), [files])

  // "Investigate potential bugs" from the Files section: select the code and start right away.
  useEffect(() => {
    if (!target?.file_path) return
    const key = target.symbol ? (target.parent ? `${target.parent}.${target.symbol}` : target.symbol) : ''
    setFilePath(target.file_path)
    setSymbol(key)
    run(true, { file_path: target.file_path, symbol: target.symbol ?? null, parent: target.parent ?? null })
  }, [target]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setSymbols([])
    if (!paths.has(filePath)) return
    let current = true
    getSymbols(repositoryId, filePath)
      .then((d) => current && setSymbols(d.symbols))
      .catch(() => current && setSymbols([]))
    return () => {
      current = false
    }
  }, [repositoryId, filePath, paths])

  function selection() {
    if (!filePath.trim()) return { file_path: null }
    const chosen = symbols.find((s) => (s.parent ? `${s.parent}.${s.name}` : s.name) === symbol)
    return { file_path: filePath.trim(), symbol: chosen?.name ?? null, parent: chosen?.parent ?? null }
  }

  async function run(useAi, selected = selection()) {
    if (!description.trim() && !selected.file_path)
      return setError('Describe the problem, or choose a file / function to investigate.')
    setBusy(useAi ? 'ai' : 'hints')
    setError(null)
    try {
      setResult(await debugInvestigate(repositoryId, { description: description.trim(), ...selected, use_ai: useAi }))
    } catch (err) {
      setError(err.retryAfterSeconds ? `${err.message} Try again in about ${Math.ceil(err.retryAfterSeconds)} seconds.` : err.message)
    } finally {
      setBusy(null)
    }
  }

  const byId = Object.fromEntries((result?.sources ?? []).map((s) => [s.source_id, s]))
  return (
    <section className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4" data-testid="debug-panel">
      <div>
        <h3 className="text-sm font-semibold text-slate-200">Debug Investigation</h3>
        <p className="mt-0.5 text-sm text-slate-500">
          Describe a bug; RepoMind retrieves related code, runs pattern checks and asks the AI for possible causes.
          Findings are <span className="text-slate-300">hypotheses to verify</span>. RepoMind never changes your code.
        </p>
      </div>
      <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); run(true) }}>
        <textarea aria-label="Problem description" rows={3} maxLength={MAX} value={description} onChange={(e) => setDescription(e.target.value)}
          placeholder="Describe the problem (optional when a file or function is selected), e.g. users are redirected to the login page after posting"
          className={`${ctl} w-full resize-y`} />
        <div className="flex flex-wrap items-center gap-2">
          <input aria-label="Pinned file" list="debug-files" value={filePath} onChange={(e) => setFilePath(e.target.value)}
            placeholder="optional: file to focus on" className={`${ctl} min-w-0 flex-1 font-mono`} />
          <datalist id="debug-files">{files.filter((f) => f.type === 'file').slice(0, 5000).map((f) => <option key={f.path} value={f.path} />)}</datalist>
          <select aria-label="Function" className={ctl} value={symbol} onChange={(e) => setSymbol(e.target.value)} disabled={!symbols.length}>
            <option value="">{symbols.length ? 'Whole file' : 'No functions'}</option>
            {symbols.map((s) => {
              const key = s.parent ? `${s.parent}.${s.name}` : s.name
              return <option key={`${key}:${s.start_line}`} value={key}>{s.kind} {key}</option>
            })}
          </select>
          {selectedPath && selectedPath !== filePath && (
            <button type="button" onClick={() => setFilePath(selectedPath)} className="rounded-md border border-slate-700 px-2 py-1.5 text-xs text-slate-300 hover:border-cyan-500/50" title={selectedPath}>
              Use open file
            </button>
          )}
          <span className="font-mono text-[11px] text-slate-600">{description.length}/{MAX}</span>
          <button type="button" disabled={!!busy} onClick={() => run(false)} className="rounded-lg border border-slate-700 px-4 py-1.5 text-sm text-slate-200 hover:border-cyan-500/50 disabled:opacity-60"
            title="Retrieve related code and run pattern checks without calling the AI">
            {busy === 'hints' ? 'Searching…' : 'Rule hints only'}
          </button>
          <button type="submit" disabled={!!busy} className="rounded-lg bg-cyan-500 px-4 py-1.5 text-sm font-semibold text-slate-950 hover:bg-cyan-400 disabled:opacity-60">
            {busy === 'ai' ? 'Investigating…' : 'Investigate potential bugs'}
          </button>
        </div>
      </form>
      {error && <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{error}</p>}

      {result && (
        <div className="space-y-4 border-t border-slate-800 pt-4" data-testid="debug-result">
          {result.summary ? (
            <div data-testid="debug-summary">
              <h4 className="mb-1 text-sm font-semibold text-slate-100">Possible causes <span className="font-normal text-slate-500">(AI, needs verification)</span></h4>
              <Markdown text={result.summary} renderCitation={(id) => <CitationChip id={id} source={byId[id]} onOpenSource={onOpenSource} />} />
            </div>
          ) : (
            result.ai.status !== 'not_requested' && (
              <p className="text-xs text-amber-300">
                {result.ai.status === 'no_evidence' ? 'No related code was found, so the AI was not asked.' : 'The AI response could not be parsed; only rule hints are shown.'}
              </p>
            )
          )}
          {result.truncated && <p className="text-xs text-amber-300">The AI answer was cut off at the model's output limit.</p>}
          <EvidenceNotice evidence={result.summary ? result.evidence : null} citations={result.citations} />
          {result.message && <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">{result.message}</p>}
          {result.ai.rejected > 0 && (
            <p className="text-xs text-slate-500">{result.ai.rejected} AI finding{result.ai.rejected > 1 ? 's were' : ' was'} dropped because it did not point at the retrieved code.</p>
          )}
          <div className="space-y-1">
            <h4 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">Potential findings ({result.findings.length})</h4>
            <FindingsList findings={result.findings} onOpenSource={onOpenSource} emptyText="No pattern hints or AI findings for the retrieved code." />
          </div>
          <SourcesPanel sources={result.sources} onOpenSource={onOpenSource} title="Code examined" />
          <p className="font-mono text-[11px] text-slate-600">
            {result.model ?? 'no AI call'} · {(result.took_ms / 1000).toFixed(1)}s
            {result.usage?.input_tokens != null && ` · ${result.usage.input_tokens.toLocaleString()} in / ${(result.usage.output_tokens ?? 0).toLocaleString()} out tokens`}
          </p>
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">Limitations</summary>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">{result.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
          </details>
        </div>
      )}
    </section>
  )
}
