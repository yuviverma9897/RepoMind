import { useMemo, useState } from 'react'

// Shared list for Debug and Security findings. Every finding is a *potential* issue that needs review;
// clicking the location opens it in the code viewer.

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info']
const SEVERITY_STYLE = {
  critical: 'bg-rose-500/20 text-rose-200 border-rose-500/40',
  high: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
  medium: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  low: 'bg-sky-500/10 text-sky-300 border-sky-500/30',
  info: 'bg-slate-800 text-slate-400 border-slate-700',
}
const VERDICT = {
  likely_issue: ['AI: likely issue', 'text-rose-300'],
  likely_false_positive: ['AI: likely false positive', 'text-emerald-300'],
  uncertain: ['AI: uncertain', 'text-slate-400'],
}
const ctl = 'rounded-md border border-slate-700 bg-slate-950/70 px-2 py-1 text-xs text-slate-200 focus:border-cyan-500/60 focus:outline-none'
const label = (s) => s.replaceAll('_', ' ')

export function SeverityBadge({ severity }) {
  return (
    <span className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase ${SEVERITY_STYLE[severity]}`}>
      {severity}
    </span>
  )
}

function FindingCard({ f, onOpen }) {
  const [open, setOpen] = useState(false)
  const verdict = f.ai_review && VERDICT[f.ai_review.verdict]
  return (
    <li className="px-3 py-2.5" data-testid="finding" data-finding-id={f.id}>
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={f.severity} />
        <button type="button" onClick={() => setOpen(!open)} className="min-w-0 text-left text-sm font-medium text-slate-100 hover:text-cyan-300" aria-expanded={open}>
          {f.title}
        </button>
        <span className="font-mono text-[11px] text-slate-500">{label(f.category)}</span>
        <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${f.origin === 'ai' ? 'bg-violet-500/15 text-violet-300' : 'bg-slate-800 text-slate-400'}`}>
          {f.origin === 'ai' ? 'AI' : `rule ${f.rule_id}`}
        </span>
        <span className="font-mono text-[10px] text-slate-500">confidence {f.confidence}</span>
        {f.non_production && <span className="font-mono text-[10px] text-slate-500" title="Test, example, docs or build/CI code">· non-production</span>}
        {verdict && <span className={`text-[11px] ${verdict[1]}`}>{verdict[0]}</span>}
      </div>
      <button
        type="button"
        onClick={() => onOpen({ file_path: f.file_path, start_line: f.start_line, end_line: f.end_line })}
        className="mt-1 font-mono text-xs text-cyan-300/90 hover:text-cyan-200"
        title="Open in the code viewer"
      >
        {f.file_path}:{f.start_line}{f.end_line !== f.start_line && `–${f.end_line}`}
      </button>
      {f.snippet && <code className="mt-1 block truncate rounded bg-slate-950/70 px-2 py-1 font-mono text-[11px] text-slate-400">{f.snippet}</code>}
      {open && (
        <div className="mt-2 space-y-1.5 text-xs leading-relaxed text-slate-300">
          <p>{f.explanation}</p>
          {f.reasoning && <p><span className="text-slate-500">Reasoning: </span>{f.reasoning}</p>}
          {f.ai_review?.reasoning && <p><span className="text-slate-500">AI review: </span>{f.ai_review.reasoning}</p>}
          <p><span className="text-slate-500">Suggestion: </span>{f.recommendation}</p>
          <p className="text-[11px] text-amber-300/80">Requires review: this is a potential finding, not a confirmed problem.</p>
        </div>
      )}
    </li>
  )
}

export default function FindingsList({ findings, onOpenSource, emptyText = 'No findings.' }) {
  const [severity, setSeverity] = useState('')
  const [category, setCategory] = useState('')
  const [origin, setOrigin] = useState('')
  const [hideNonProduction, setHideNonProduction] = useState(false)
  const categories = useMemo(() => [...new Set(findings.map((f) => f.category))].sort(), [findings])
  const shown = findings.filter(
    (f) =>
      (!severity || SEVERITIES.indexOf(f.severity) <= SEVERITIES.indexOf(severity)) &&
      (!category || f.category === category) &&
      (!origin || f.origin === origin) &&
      !(hideNonProduction && f.non_production),
  )
  if (!findings.length) return <p className="text-sm text-slate-500">{emptyText}</p>
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <select aria-label="Minimum severity" className={ctl} value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="">All severities</option>
          {SEVERITIES.slice(0, 4).map((s) => <option key={s} value={s}>{s} and above</option>)}
        </select>
        <select aria-label="Category" className={ctl} value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {categories.map((c) => <option key={c} value={c}>{label(c)}</option>)}
        </select>
        <select aria-label="Origin" className={ctl} value={origin} onChange={(e) => setOrigin(e.target.value)}>
          <option value="">Rules + AI</option>
          <option value="rule">Rules only</option>
          <option value="ai">AI only</option>
        </select>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={hideNonProduction} onChange={(e) => setHideNonProduction(e.target.checked)} />
          Hide tests / examples / tooling
        </label>
        <span className="ml-auto font-mono text-slate-500">{shown.length} of {findings.length} shown</span>
      </div>
      <ul className="max-h-[36rem] divide-y divide-slate-800/70 overflow-y-auto rounded-lg border border-slate-800" data-testid="findings-list">
        {shown.map((f) => <FindingCard key={f.id} f={f} onOpen={onOpenSource} />)}
      </ul>
    </div>
  )
}
