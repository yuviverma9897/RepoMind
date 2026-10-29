import { useEffect, useState } from 'react'
import { askRepository, explainCode, getSymbols } from '../api'
import GroundedAnswer from './GroundedAnswer'

// Explain Code / Explain Function / Ask About This Code for the file open in the viewer.

function StructureFacts({ structure, note }) {
  if (!structure) {
    return note ? <p className="text-xs text-slate-500">{note}</p> : null
  }
  const rows = []
  if (structure.receiver) rows.push(['Receiver', structure.receiver])
  if (structure.parameters?.length) {
    rows.push([
      'Parameters',
      structure.parameters
        .map((p) => [p.name, p.type && `: ${p.type}`, p.default != null && ` = ${p.default}`].filter(Boolean).join(''))
        .join(', '),
    ])
  }
  if (structure.returns) rows.push(['Returns', structure.returns])
  if (structure.bases?.length) rows.push(['Bases', structure.bases.join(', ')])
  if (structure.extends) rows.push(['Extends', structure.extends])
  for (const key of ['methods', 'members', 'attributes', 'functions', 'imports', 'decorators', 'raises']) {
    if (structure[key]?.length) rows.push([key[0].toUpperCase() + key.slice(1), structure[key].join(', ')])
  }
  if (structure.classes?.length) rows.push(['Classes', structure.classes.map((c) => c.name).join(', ')])
  if (structure.calls?.length) rows.push(['Calls', structure.calls.join(', ')])
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/40" data-testid="structure-facts">
      <div className="flex items-center justify-between border-b border-slate-800 px-3 py-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wider text-slate-500">Static analysis</span>
        <span className="font-mono text-[11px] text-slate-600">
          {structure.method === 'python-ast' ? 'exact (Python AST)' : 'heuristic'}
          {structure.is_async ? ' · async' : ''}
          {structure.is_generator ? ' · generator' : ''}
        </span>
      </div>
      <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1 px-3 py-2 text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd className="break-words font-mono text-slate-300">{v}</dd>
          </div>
        ))}
      </dl>
      {note && <p className="border-t border-slate-800 px-3 py-1.5 text-[11px] text-slate-500">{note}</p>}
    </div>
  )
}

const inputClass =
  'w-20 rounded-md border border-slate-700 bg-slate-950/70 px-2 py-1 font-mono text-xs text-slate-100 focus:border-cyan-500/60 focus:outline-none'
const buttonClass =
  'rounded-md border border-slate-700 px-3 py-1 text-xs font-medium text-slate-200 transition hover:border-cyan-500/50 hover:text-cyan-300 disabled:cursor-not-allowed disabled:opacity-50'

export default function CodeIntelligencePanel({ repositoryId, selectedPath, highlight, onOpenSource, onShowLines, onInvestigate }) {
  const [symbols, setSymbols] = useState(null)
  const [symbolsError, setSymbolsError] = useState(null)
  const [range, setRange] = useState({ start: '', end: '' })
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(null) // label of the running action
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null) // { kind: 'explain' | 'ask', title, data }

  useEffect(() => {
    setSymbols(null)
    setSymbolsError(null)
    setResult(null)
    setError(null)
    if (!selectedPath) return
    let current = true
    getSymbols(repositoryId, selectedPath)
      .then((data) => current && setSymbols(data))
      .catch((err) => current && setSymbolsError(err.message))
    return () => {
      current = false
    }
  }, [repositoryId, selectedPath])

  useEffect(() => {
    if (highlight) setRange({ start: String(highlight.start), end: String(highlight.end) })
  }, [highlight])

  async function run(label, action) {
    setBusy(label)
    setError(null)
    try {
      setResult(await action())
    } catch (err) {
      setError(err.retryAfterSeconds ? `${err.message} Try again in about ${Math.ceil(err.retryAfterSeconds)} seconds.` : err.message)
    } finally {
      setBusy(null)
    }
  }

  function explain(body, title) {
    run(title, async () => {
      const data = await explainCode(repositoryId, { file_path: selectedPath, ...body })
      onShowLines(data.target.start_line, data.target.end_line, data.target.kind === 'file')
      return { kind: 'explain', title, data }
    })
  }

  function selection() {
    const start = Number(range.start)
    const end = Number(range.end)
    return range.start && range.end ? { start_line: start, end_line: end } : null
  }

  if (!selectedPath) return null
  const sel = selection()

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60" data-testid="code-intelligence">
      <div className="space-y-4 px-5 py-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-200">Code Intelligence</h3>
            <p className="mt-0.5 text-sm text-slate-500">
              AI explanations of <span className="font-mono text-slate-300">{selectedPath}</span>, grounded in the
              stored code. Check the cited sources.
            </p>
          </div>
          <div className="flex gap-2">
            <button type="button" className={buttonClass} disabled={!!busy} onClick={() => explain({}, 'Explain file')}>
              Explain file
            </button>
            <button type="button" className={buttonClass} onClick={() => onInvestigate({ file_path: selectedPath })}
              title="Open Debug and look for potential bugs in this file">
              Investigate potential bugs
            </button>
          </div>
        </div>

        {symbolsError && <p className="text-xs text-amber-300">{symbolsError}</p>}
        {symbols && (
          <div className="space-y-1">
            <h4 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
              Symbols ({symbols.symbols.length})
              {!symbols.structure_support && ` · no structural parser for ${symbols.language}`}
              {symbols.structure_support === 'heuristic' && ' · heuristic parser'}
            </h4>
            {symbols.symbols.length > 0 ? (
              <ul className="max-h-56 divide-y divide-slate-800/70 overflow-y-auto rounded-lg border border-slate-800" data-testid="symbol-list">
                {symbols.symbols.map((s) => (
                  <li key={`${s.kind}:${s.parent}:${s.name}:${s.start_line}`} className="flex items-center gap-3 px-3 py-1.5">
                    <span className="w-16 shrink-0 font-mono text-[11px] text-violet-300">{s.kind}</span>
                    <button
                      type="button"
                      onClick={() => onShowLines(s.start_line, s.end_line)}
                      className="min-w-0 flex-1 truncate text-left font-mono text-xs text-slate-200 hover:text-cyan-300"
                      title="Show in viewer"
                    >
                      {s.parent ? `${s.parent}.` : ''}
                      {s.name}
                    </button>
                    <span className="shrink-0 font-mono text-[11px] text-slate-500">L{s.start_line}–{s.end_line}</span>
                    <button
                      type="button"
                      className={buttonClass}
                      disabled={!!busy}
                      onClick={() =>
                        explain({ symbol: s.name, parent: s.parent }, `Explain ${s.parent ? `${s.parent}.` : ''}${s.name}`)
                      }
                    >
                      Explain
                    </button>
                    <button type="button" className={buttonClass} title="Investigate potential bugs (opens Debug)"
                      onClick={() => onInvestigate({ file_path: selectedPath, symbol: s.name, parent: s.parent })}>
                      Bugs
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-slate-500">No functions or classes found in this file.</p>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-500">Selection</span>
          <input aria-label="Start line" className={inputClass} type="number" min="1" value={range.start}
            onChange={(e) => setRange({ ...range, start: e.target.value })} placeholder="start" />
          <span className="text-slate-600">–</span>
          <input aria-label="End line" className={inputClass} type="number" min="1" value={range.end}
            onChange={(e) => setRange({ ...range, end: e.target.value })} placeholder="end" />
          <button type="button" className={buttonClass} disabled={!!busy || !sel}
            onClick={() => explain(sel, `Explain lines ${sel.start_line}–${sel.end_line}`)}>
            Explain selection
          </button>
          <span className="text-[11px] text-slate-600">(filled from the highlighted lines; max 400)</span>
        </div>

        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault()
            const q = question.trim()
            if (!q) return setError('Enter a question about this code.')
            const focus = { file_path: selectedPath, ...(sel ?? {}) }
            run('Ask', async () => ({
              kind: 'ask',
              title: `${q}`,
              data: await askRepository(repositoryId, q, 6, focus),
            }))
          }}
        >
          <input
            aria-label="Ask about this code"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={1000}
            placeholder={sel ? `Ask about lines ${sel.start_line}–${sel.end_line}, e.g. Why does this work?` : 'Ask about this file, e.g. Why does this work?'}
            className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-1.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-cyan-500/60 focus:outline-none"
          />
          <button type="submit" disabled={!!busy} className="rounded-lg bg-cyan-500 px-4 py-1.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:opacity-60">
            Ask about this code
          </button>
        </form>

        {busy && (
          <p className="flex items-center gap-2 text-xs text-slate-400">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-cyan-400" />
            {busy}: generating…
          </p>
        )}
        {error && (
          <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{error}</p>
        )}
      </div>

      {result && (
        <div className="space-y-3 border-t border-slate-800 px-5 py-4" data-testid="intelligence-result">
          <div className="flex flex-wrap items-baseline gap-2">
            <h4 className="text-sm font-semibold text-slate-100">{result.title}</h4>
            {result.kind === 'explain' && (
              <span className="font-mono text-[11px] text-slate-500">
                {result.data.target.kind} · {result.data.target.file_path} L{result.data.target.start_line}–{result.data.target.end_line}
              </span>
            )}
          </div>
          {result.data.target?.note && <p className="text-xs text-amber-300">{result.data.target.note}</p>}
          {result.kind === 'explain' && <StructureFacts structure={result.data.structure} note={result.data.structure_note} />}
          <GroundedAnswer result={result.data} onOpenSource={onOpenSource} />
        </div>
      )}
    </section>
  )
}
