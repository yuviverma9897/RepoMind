import { useEffect, useMemo, useState } from 'react'
import { analyzeImpact, explainImpact, getSymbols } from '../api'
import GroundedAnswer from './GroundedAnswer'

const CODE_FILE = /\.(py|pyi|js|jsx|mjs|cjs|ts|tsx|mts|cts|go|java|kt|rs|rb|php|cs|c|h|cpp|hpp|swift|scala)$/
const ctl = 'rounded-md border border-slate-700 bg-slate-950/70 px-2 py-1.5 text-sm text-slate-100 focus:border-cyan-500/60 focus:outline-none'
const STATUS_STYLE = {
  resolved: 'text-emerald-300', inferred: 'text-cyan-300', external: 'text-slate-500', unresolved: 'text-amber-300',
}

function Section({ title, count, children, empty }) {
  return (
    <div className="space-y-1">
      <h4 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">{title} ({count})</h4>
      {count ? children : <p className="text-xs text-slate-600">{empty}</p>}
    </div>
  )
}

export default function ImpactPanel({ repositoryId, files, target, onOpenSource }) {
  const codeFiles = useMemo(() => files.filter((f) => f.type === 'file' && CODE_FILE.test(f.path)).map((f) => f.path), [files])
  const [filePath, setFilePath] = useState('')
  const [symbols, setSymbols] = useState([])
  const [symbol, setSymbol] = useState('')
  const [depth, setDepth] = useState(2)
  const [result, setResult] = useState(null)
  const [ai, setAi] = useState(null)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (target?.file_path) {
      setFilePath(target.file_path)
      run('analyze', target.file_path, '')
    }
  }, [target]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setSymbols([])
    setSymbol('')
    if (!codeFiles.includes(filePath)) return
    let current = true
    getSymbols(repositoryId, filePath)
      .then((d) => current && setSymbols(d.symbols.filter((s) => !s.parent)))
      .catch(() => current && setSymbols([]))
    return () => {
      current = false
    }
  }, [repositoryId, filePath, codeFiles])

  async function run(kind, path = filePath, sym = symbol) {
    if (!path.trim()) return setError('Choose a file.')
    const body = { file_path: path.trim(), symbol: sym || null, depth }
    setBusy(kind)
    setError(null)
    if (kind === 'analyze') setAi(null)
    try {
      if (kind === 'analyze') setResult(await analyzeImpact(repositoryId, body))
      else {
        const data = await explainImpact(repositoryId, body)
        setAi(data)
        setResult(data.impact)
      }
    } catch (err) {
      setError(err.retryAfterSeconds ? `${err.message} Try again in about ${Math.ceil(err.retryAfterSeconds)} seconds.` : err.message)
    } finally {
      setBusy(null)
    }
  }

  const open = (file_path, line) => onOpenSource({ file_path, start_line: line, end_line: line })

  return (
    <section className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4" data-testid="impact-panel">
      <div>
        <h3 className="text-sm font-semibold text-slate-200">Impact Analysis</h3>
        <p className="mt-0.5 text-sm text-slate-500">
          What may be affected if a file or function changes. AI-assisted and based on static imports and name matches.
          It is <span className="text-slate-300">not guaranteed to be complete</span>.
        </p>
      </div>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          run('analyze')
        }}
      >
        <input aria-label="File path" list="impact-files" value={filePath} onChange={(e) => setFilePath(e.target.value)}
          placeholder="file path, e.g. src/app.py" className={`${ctl} min-w-0 flex-1 font-mono`} />
        <datalist id="impact-files">{codeFiles.slice(0, 5000).map((p) => <option key={p} value={p} />)}</datalist>
        <select aria-label="Symbol" className={ctl} value={symbol} onChange={(e) => setSymbol(e.target.value)} disabled={!symbols.length}>
          <option value="">{symbols.length ? 'Whole file' : 'No symbols'}</option>
          {symbols.map((s) => <option key={`${s.name}:${s.start_line}`} value={s.name}>{s.kind} {s.name}</option>)}
        </select>
        <select aria-label="Depth" className={ctl} value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
          {[1, 2, 3].map((d) => <option key={d} value={d}>depth {d}</option>)}
        </select>
        <button type="submit" disabled={!!busy} className="rounded-lg border border-slate-700 px-4 py-1.5 text-sm text-slate-200 hover:border-cyan-500/50 disabled:opacity-60">
          {busy === 'analyze' ? 'Analyzing…' : 'Analyze impact'}
        </button>
        <button type="button" disabled={!!busy} onClick={() => run('explain')} className="rounded-lg bg-cyan-500 px-4 py-1.5 text-sm font-semibold text-slate-950 hover:bg-cyan-400 disabled:opacity-60">
          {busy === 'explain' ? 'Explaining…' : 'Explain with AI'}
        </button>
      </form>
      {error && <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{error}</p>}

      {result && (
        <div className="space-y-4 border-t border-slate-800 pt-4" data-testid="impact-result">
          <p className="text-sm text-slate-300">
            <span className="font-mono">{result.file_path}</span>{result.symbol && <> · <span className="font-mono text-cyan-300">{result.symbol}</span></>}:{' '}
            {result.direct_dependents.length} direct and {result.transitive_dependents.length} indirect dependent files
            {result.symbol && `, ${result.references.length} name references`}.
          </p>
          {!result.imports_supported && (
            <p className="text-xs text-amber-300">Imports of {result.language} files are not analyzed; dependents may be missing.</p>
          )}
          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="Direct dependents" count={result.direct_dependents.length} empty="No file imports this one (as far as static analysis can tell).">
              <ul className="space-y-0.5">{result.direct_dependents.map((d) => (
                <li key={d.file_path}><button type="button" className="font-mono text-xs text-slate-200 hover:text-cyan-300" onClick={() => open(d.file_path, d.lines[0])}>{d.file_path}<span className="text-slate-500"> :{d.lines.join(',')}</span></button></li>
              ))}</ul>
            </Section>
            <Section title="Indirect dependents" count={result.transitive_dependents.length} empty={`None within depth ${result.depth}.`}>
              <ul className="max-h-48 space-y-0.5 overflow-y-auto">{result.transitive_dependents.map((d) => (
                <li key={d.file_path} className="text-xs"><button type="button" className="font-mono text-slate-200 hover:text-cyan-300" onClick={() => open(d.file_path, d.lines[0])}>{d.file_path}</button> <span className="text-slate-600">· distance {d.distance} via {d.via}</span></li>
              ))}</ul>
            </Section>
            {result.symbol && (
              <Section title={`References to ${result.symbol}`} count={result.references.length} empty="No other mentions found.">
                <ul className="max-h-56 space-y-0.5 overflow-y-auto">{result.references.map((r) => (
                  <li key={`${r.file_path}:${r.line}`} className="text-xs">
                    <button type="button" className="font-mono text-slate-200 hover:text-cyan-300" onClick={() => open(r.file_path, r.line)}>{r.file_path}:{r.line}</button>
                    {!r.file_imports_target && !r.same_file && <span className="ml-1 text-amber-300/80" title="This file does not import the target; may be a different symbol with the same name">· name match only</span>}
                    <code className="block truncate text-slate-500">{r.text}</code>
                  </li>
                ))}</ul>
              </Section>
            )}
            <Section title="This file depends on" count={result.dependencies.length} empty="No imports found.">
              <ul className="max-h-48 space-y-0.5 overflow-y-auto">{result.dependencies.map((d) => (
                <li key={`${d.line}:${d.specifier}`} className="font-mono text-xs text-slate-300">{d.specifier} <span className={STATUS_STYLE[d.status]}>· {d.status}</span>{d.target && d.target !== d.specifier && <span className="text-slate-600"> → {d.target}</span>}</li>
              ))}</ul>
            </Section>
          </div>
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">Limitations</summary>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">{result.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
          </details>
        </div>
      )}
      {ai && (
        <div className="space-y-2 border-t border-slate-800 pt-4" data-testid="impact-ai">
          <h4 className="text-sm font-semibold text-slate-100">AI impact assessment <span className="font-normal text-slate-500">(potential impact, not a guarantee)</span></h4>
          <GroundedAnswer result={ai} onOpenSource={onOpenSource} />
        </div>
      )}
    </section>
  )
}
