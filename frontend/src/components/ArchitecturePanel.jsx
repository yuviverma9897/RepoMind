import { useEffect, useMemo, useState } from 'react'
import { getDependencyGraph, summarizeArchitecture } from '../api'
import GroundedAnswer from './GroundedAnswer'

// Dependency graph as a plain SVG with a layered layout (importers left, dependencies right).
// The backend caps the graph (<= 300 nodes), so no graph library is needed.

const NODE_W = 190
const NODE_H = 28
const COL_GAP = 110
const ROW_GAP = 12
const PAD = 16

function layout(nodes, edges) {
  const out = new Map(nodes.map((n) => [n.id, []]))
  for (const e of edges) out.get(e.source)?.push(e.target)
  // depth = longest path to a node without dependencies; back edges of cycles are ignored
  const depth = new Map()
  const state = new Map()
  const visit = (id) => {
    if (state.get(id) === 'done') return depth.get(id)
    if (state.get(id) === 'active') return -1 // cycle
    state.set(id, 'active')
    let d = 0
    for (const t of out.get(id) ?? []) d = Math.max(d, visit(t) + 1)
    state.set(id, 'done')
    depth.set(id, d)
    return d
  }
  nodes.forEach((n) => visit(n.id))
  const maxDepth = Math.max(0, ...depth.values())
  const columns = Array.from({ length: maxDepth + 1 }, () => [])
  for (const n of nodes) columns[maxDepth - depth.get(n.id)].push(n)
  // order each column by the average row of its neighbours in the previous column (fewer crossings)
  const row = new Map()
  columns.forEach((col, ci) => {
    if (ci > 0) {
      const score = (n) => {
        const ns = edges.filter((e) => e.target === n.id && row.has(e.source)).map((e) => row.get(e.source))
        return ns.length ? ns.reduce((a, b) => a + b, 0) / ns.length : Infinity
      }
      col.sort((a, b) => score(a) - score(b) || b.in_degree + b.out_degree - (a.in_degree + a.out_degree))
    }
    col.forEach((n, i) => row.set(n.id, i))
  })
  const pos = new Map()
  columns.forEach((col, ci) =>
    col.forEach((n, i) => pos.set(n.id, { x: PAD + ci * (NODE_W + COL_GAP), y: PAD + i * (NODE_H + ROW_GAP) })),
  )
  const rows = Math.max(1, ...columns.map((c) => c.length))
  return {
    pos,
    width: PAD * 2 + columns.length * NODE_W + (columns.length - 1) * COL_GAP,
    height: PAD * 2 + rows * (NODE_H + ROW_GAP),
  }
}

function edgePath(a, b) {
  const x1 = a.x + NODE_W
  const y1 = a.y + NODE_H / 2
  const x2 = b.x
  const y2 = b.y + NODE_H / 2
  if (x2 > x1) {
    const mid = (x1 + x2) / 2
    return `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`
  }
  // same column or backwards (cycle): arc around
  const lift = 30 + Math.abs(y2 - y1) / 4
  return `M${a.x + NODE_W / 2},${a.y} C${a.x + NODE_W / 2},${a.y - lift} ${b.x + NODE_W / 2},${b.y - lift} ${b.x + NODE_W / 2},${b.y}`
}

const LANG_COLORS = { python: '#38bdf8', go: '#2dd4bf', typescript: '#818cf8', tsx: '#818cf8', javascript: '#facc15', jsx: '#facc15' }

function Graph({ graph, selected, onSelect }) {
  const [hover, setHover] = useState(null)
  const { pos, width, height } = useMemo(() => layout(graph.nodes, graph.edges), [graph])
  const focus = hover ?? selected
  const linked = (e) => focus && (e.source === focus || e.target === focus)
  return (
    <div className="max-h-[560px] overflow-auto rounded-lg border border-slate-800 bg-slate-950/60" data-testid="dependency-graph">
      <svg width={width} height={height} role="img" aria-label="Dependency graph">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="#475569" />
          </marker>
          <marker id="arrow-hi" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="#22d3ee" />
          </marker>
        </defs>
        {graph.edges.map((e) => {
          const a = pos.get(e.source)
          const b = pos.get(e.target)
          if (!a || !b) return null
          const hi = linked(e)
          return (
            <path
              key={`${e.source}->${e.target}`}
              d={edgePath(a, b)}
              fill="none"
              stroke={hi ? '#22d3ee' : '#334155'}
              strokeOpacity={focus && !hi ? 0.15 : 0.9}
              strokeWidth={Math.min(4, 1 + Math.log2(e.weight))}
              markerEnd={`url(#${hi ? 'arrow-hi' : 'arrow'})`}
            >
              <title>{`${e.source} → ${e.target} (${e.weight} import${e.weight > 1 ? 's' : ''})`}</title>
            </path>
          )
        })}
        {graph.nodes.map((n) => {
          const p = pos.get(n.id)
          const isSel = n.id === selected
          const dim = focus && focus !== n.id && !graph.edges.some((e) => linked(e) && (e.source === n.id || e.target === n.id))
          return (
            <g
              key={n.id}
              transform={`translate(${p.x},${p.y})`}
              onMouseEnter={() => setHover(n.id)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelect(n.id === selected ? null : n.id)}
              className="cursor-pointer"
              opacity={dim ? 0.35 : 1}
              data-node={n.id}
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx="6"
                fill={isSel ? '#0e7490' : '#0f172a'}
                stroke={isSel ? '#22d3ee' : n.type === 'external' ? '#64748b' : '#334155'}
                strokeDasharray={n.type === 'external' ? '4 3' : undefined}
              />
              <circle cx="11" cy={NODE_H / 2} r="3.5" fill={n.type === 'external' ? '#64748b' : LANG_COLORS[n.language] ?? '#94a3b8'} />
              <text x="21" y={NODE_H / 2 + 4} fontSize="11.5" fontFamily="ui-monospace, monospace" fill="#e2e8f0">
                {n.label.length > 24 ? `…${n.label.slice(-23)}` : n.label}
              </text>
              <title>{`${n.id}\n${n.type}${n.files ? ` · ${n.files} files` : ''} · imports ${n.out_degree} · imported by ${n.in_degree}`}</title>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

const ctl = 'rounded-md border border-slate-700 bg-slate-950/70 px-2 py-1 text-xs text-slate-200 focus:border-cyan-500/60 focus:outline-none'

export default function ArchitecturePanel({ repositoryId, onOpenFile, onAnalyzeImpact, onOpenSource }) {
  const [opts, setOpts] = useState({ level: 'directory', depth: 2, pathPrefix: '', maxNodes: 80, includeExternal: false })
  const [graph, setGraph] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [summary, setSummary] = useState(null)
  const [summaryState, setSummaryState] = useState({ loading: false, error: null })

  async function load(next = opts) {
    setLoading(true)
    setError(null)
    setSelected(null)
    try {
      setGraph(await getDependencyGraph(repositoryId, next))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [repositoryId]) // eslint-disable-line react-hooks/exhaustive-deps

  function update(change) {
    const next = { ...opts, ...change }
    setOpts(next)
    load(next)
  }

  async function summarize() {
    setSummaryState({ loading: true, error: null })
    try {
      setSummary(await summarizeArchitecture(repositoryId, opts.pathPrefix))
      setSummaryState({ loading: false, error: null })
    } catch (err) {
      setSummaryState({ loading: false, error: err.message })
    }
  }

  const node = graph?.nodes.find((n) => n.id === selected)
  const outgoing = graph?.edges.filter((e) => e.source === selected) ?? []
  const incoming = graph?.edges.filter((e) => e.target === selected) ?? []
  const stats = graph?.stats
  const unsupported = stats ? Object.entries(stats.unsupported_languages) : []

  return (
    <section className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4" data-testid="architecture-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-200">Architecture</h3>
          <p className="mt-0.5 text-sm text-slate-500">
            Import graph from static analysis of Python, JavaScript/TypeScript and Go. Arrows point from importer to
            dependency.
          </p>
        </div>
        <button type="button" onClick={summarize} disabled={summaryState.loading}
          className="rounded-lg bg-cyan-500 px-4 py-1.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:opacity-60">
          {summaryState.loading ? 'Summarizing…' : 'AI architecture summary'}
        </button>
      </div>

      <form
        className="flex flex-wrap items-center gap-2 text-xs text-slate-400"
        onSubmit={(e) => {
          e.preventDefault()
          load()
        }}
      >
        <label className="flex items-center gap-1">
          View
          <select aria-label="Graph level" className={ctl} value={opts.level} onChange={(e) => update({ level: e.target.value })}>
            <option value="directory">Directories</option>
            <option value="file">Files</option>
          </select>
        </label>
        {opts.level === 'directory' && (
          <label className="flex items-center gap-1">
            Depth
            <select aria-label="Directory depth" className={ctl} value={opts.depth} onChange={(e) => update({ depth: Number(e.target.value) })}>
              {[1, 2, 3, 4].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
        )}
        <label className="flex items-center gap-1">
          Max nodes
          <select aria-label="Max nodes" className={ctl} value={opts.maxNodes} onChange={(e) => update({ maxNodes: Number(e.target.value) })}>
            {[40, 80, 150].map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={opts.includeExternal} onChange={(e) => update({ includeExternal: e.target.checked })} />
          External packages
        </label>
        <input aria-label="Path prefix" className={`${ctl} w-48 font-mono`} placeholder="path prefix, e.g. src/"
          value={opts.pathPrefix} onChange={(e) => setOpts({ ...opts, pathPrefix: e.target.value })} />
        <button type="submit" className="rounded-md border border-slate-700 px-3 py-1 text-slate-200 hover:border-cyan-500/50">Apply</button>
      </form>

      {error && <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{error}</p>}
      {loading && <p className="text-xs text-slate-500">Building dependency graph…</p>}

      {graph && !loading && (
        <>
          <p className="font-mono text-[11px] text-slate-500" data-testid="graph-stats">
            {graph.nodes.length} of {graph.total_nodes} nodes · {graph.edges.length} edges · {stats.files_analyzed} files analyzed ·
            imports: {stats.resolved} resolved, {stats.inferred} inferred, {stats.external} external, {stats.unresolved} unresolved
          </p>
          {graph.truncated && (
            <p className="text-xs text-amber-300">
              Showing the {graph.nodes.length} most connected nodes. Use a path prefix or the directory view to explore the rest.
            </p>
          )}
          {unsupported.length > 0 && (
            <p className="text-xs text-amber-300/90" data-testid="unsupported-languages">
              Not analyzed (unsupported languages): {unsupported.map(([l, c]) => `${l} (${c} files)`).join(', ')}.
            </p>
          )}
          {graph.nodes.length === 0 ? (
            <p className="text-sm text-slate-500">No imports between analyzed files were found{opts.pathPrefix ? ' under this prefix' : ''}.</p>
          ) : (
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
              <Graph graph={graph} selected={selected} onSelect={setSelected} />
              <aside className="space-y-3 rounded-lg border border-slate-800 bg-slate-950/40 p-3 text-xs" data-testid="node-details">
                {node ? (
                  <>
                    <div>
                      <p className="break-all font-mono text-sm text-slate-100">{node.id}</p>
                      <p className="mt-0.5 text-slate-500">{node.type}{node.language ? ` · ${node.language}` : ''}{node.files ? ` · ${node.files} files` : ''}</p>
                    </div>
                    {node.type === 'file' && (
                      <div className="flex gap-2">
                        <button type="button" className="rounded-md border border-slate-700 px-2 py-1 text-slate-200 hover:border-cyan-500/50" onClick={() => onOpenFile(node.id)}>Open in viewer</button>
                        <button type="button" className="rounded-md border border-slate-700 px-2 py-1 text-slate-200 hover:border-cyan-500/50" onClick={() => onAnalyzeImpact(node.id)}>Analyze impact</button>
                      </div>
                    )}
                    {node.type === 'directory' && (
                      <button type="button" className="rounded-md border border-slate-700 px-2 py-1 text-slate-200 hover:border-cyan-500/50"
                        onClick={() => update({ level: 'file', pathPrefix: node.id === '/' ? '' : node.id })}>
                        Show files in this directory
                      </button>
                    )}
                    <div>
                      <h4 className="mb-1 font-mono text-[11px] uppercase tracking-wider text-slate-500">Imports ({outgoing.length})</h4>
                      <ul className="space-y-0.5">{outgoing.map((e) => <li key={e.target} className="break-all font-mono text-slate-300">→ {e.target} <span className="text-slate-600">×{e.weight}</span></li>)}</ul>
                    </div>
                    <div>
                      <h4 className="mb-1 font-mono text-[11px] uppercase tracking-wider text-slate-500">Imported by ({incoming.length})</h4>
                      <ul className="space-y-0.5">{incoming.map((e) => <li key={e.source} className="break-all font-mono text-slate-300">← {e.source} <span className="text-slate-600">×{e.weight}</span></li>)}</ul>
                    </div>
                  </>
                ) : (
                  <p className="text-slate-500">Hover a node to highlight its links; click it for details.</p>
                )}
              </aside>
            </div>
          )}
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">Limitations</summary>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">{graph.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
          </details>
        </>
      )}

      {summaryState.error && <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{summaryState.error}</p>}
      {summary && (
        <div className="space-y-2 border-t border-slate-800 pt-4" data-testid="architecture-summary">
          <h4 className="text-sm font-semibold text-slate-100">AI architecture summary{summary.path_prefix ? ` · ${summary.path_prefix}` : ''}</h4>
          <GroundedAnswer result={summary} onOpenSource={onOpenSource} />
        </div>
      )}
    </section>
  )
}
