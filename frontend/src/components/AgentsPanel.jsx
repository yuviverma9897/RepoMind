import { useEffect, useState } from 'react'
import { getAgents } from '../api'
import { ActivityTable, StatusBadge } from './AgentActivity'

const LLM_LABEL = { never: 'no AI model', optional: 'AI optional', always: 'uses AI model' }

export default function AgentsPanel({ runs, onOpenCopilot }) {
  const [agents, setAgents] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getAgents().then(setAgents).catch((err) => setError(err.message))
  }, [])

  const used = Object.fromEntries((runs ?? []).map((r) => [r.agent, (runs ?? []).filter((x) => x.agent === r.agent).length]))
  return (
    <section className="space-y-5 rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4" data-testid="agents-panel">
      <div>
        <h3 className="text-sm font-semibold text-slate-200">Agents</h3>
        <p className="mt-0.5 text-sm text-slate-500">
          A rule-based orchestrator routes each Copilot request to one specialist agent. Agents call RepoMind's existing
          analysis services; the log below lists the steps that actually ran in this session (nothing is stored).
        </p>
      </div>
      {error && <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{error}</p>}
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {(agents ?? []).map((a) => (
          <div key={a.name} className="rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2.5">
            <div className="flex items-center justify-between gap-2">
              <h4 className="text-sm font-medium text-slate-200">{a.title}</h4>
              {used[a.name] > 0 && <span className="font-mono text-[10px] text-cyan-300">{used[a.name]}× used</span>}
            </div>
            <p className="mt-1 text-xs text-slate-500">{a.description}</p>
            <p className="mt-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-600">{LLM_LABEL[a.uses_llm]}</p>
          </div>
        ))}
      </div>
      <div className="space-y-3">
        <h4 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">Activity log ({runs.length} requests)</h4>
        {runs.length === 0 ? (
          <p className="text-sm text-slate-500">
            No agent activity yet.{' '}
            <button type="button" onClick={onOpenCopilot} className="text-cyan-300 hover:underline">Ask the Copilot</button> to run an agent.
          </p>
        ) : (
          runs.map((r, i) => (
            <article key={i} className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2.5" data-testid="agent-run">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-slate-100">{r.query}</span>
                <span className="text-slate-600">→</span>
                <span className="text-cyan-300">{r.agent_title}</span>
                <StatusBadge status={r.status} />
                <span className="ml-auto font-mono text-[11px] text-slate-500">{r.took_ms} ms total</span>
              </div>
              <p className="text-xs text-slate-500">Routing: {r.routing.reason}</p>
              <ActivityTable steps={r.activity} />
            </article>
          ))
        )}
      </div>
    </section>
  )
}
