// Shared pieces for showing which agent ran and the real steps it took (from the backend's activity log).

const STATUS_STYLE = {
  completed: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  partial: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  failed: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
  unsupported: 'border-slate-600 bg-slate-800/60 text-slate-300',
  needs_input: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
  skipped: 'border-slate-700 bg-slate-900 text-slate-500',
  running: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
}

export function StatusBadge({ status }) {
  return (
    <span className={`rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase ${STATUS_STYLE[status] ?? STATUS_STYLE.skipped}`}>
      {status.replace('_', ' ')}
    </span>
  )
}

export function AgentFlow({ steps }) {
  return (
    <ol className="flex flex-wrap items-center gap-1.5 font-mono text-[11px]" data-testid="agent-flow">
      {steps.map((s, i) => (
        <li key={i} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-slate-600">→</span>}
          <span className={`rounded border px-1.5 py-0.5 ${STATUS_STYLE[s.status] ?? STATUS_STYLE.skipped}`} title={s.result ?? ''}>
            {s.agent} · {s.task.length > 42 ? `${s.task.slice(0, 41)}…` : s.task}
            {s.duration_ms != null && s.status !== 'skipped' && <span className="text-slate-500"> {s.duration_ms} ms</span>}
          </span>
        </li>
      ))}
    </ol>
  )
}

export function ActivityTable({ steps }) {
  return (
    <table className="w-full table-fixed text-left text-xs">
      <thead className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
        <tr>
          <th className="w-32 py-1 pr-2">Agent</th>
          <th className="py-1 pr-2">Task</th>
          <th className="w-24 py-1 pr-2">Status</th>
          <th className="w-20 py-1 pr-2 text-right">Time</th>
          <th className="hidden py-1 md:table-cell">Result</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-800/70">
        {steps.map((s, i) => (
          <tr key={i} className="align-top">
            <td className="py-1.5 pr-2 font-mono text-slate-300">{s.agent}</td>
            <td className="break-words py-1.5 pr-2 text-slate-300">{s.task}</td>
            <td className="py-1.5 pr-2"><StatusBadge status={s.status} /></td>
            <td className="py-1.5 pr-2 text-right font-mono text-slate-500">{s.status === 'skipped' ? '—' : `${s.duration_ms} ms`}</td>
            <td className="hidden break-words py-1.5 text-slate-400 md:table-cell">{s.result}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
