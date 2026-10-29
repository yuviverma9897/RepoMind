import Markdown from './Markdown'

// Shared rendering for every AI result grounded in numbered sources ([S1], [S2], ...):
// answer text with clickable citations, evidence warnings, the Sources panel and model metadata.

function symbolLabel(source) {
  if (!source.symbol_name) return source.chunk_type
  return `${source.chunk_type} ${source.parent_symbol ? `${source.parent_symbol}.` : ''}${source.symbol_name}`
}

export function SourcesPanel({ sources, onOpenSource, title = 'Sources' }) {
  if (!sources?.length) return null
  const cited = sources.filter((s) => s.cited).length
  return (
    <div className="space-y-1" data-testid="sources-panel">
      <h4 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
        {title} ({sources.length}{cited ? ` · ${cited} cited` : ''})
      </h4>
      <ul className="divide-y divide-slate-800/70 rounded-lg border border-slate-800">
        {sources.map((s) => (
          <li key={s.source_id} id={`source-${s.source_id}`}>
            <button
              type="button"
              onClick={() => onOpenSource(s)}
              title={`Open ${s.file_path} at lines ${s.start_line}–${s.end_line}`}
              className="flex w-full items-center gap-3 px-3 py-2 text-left transition hover:bg-slate-800/40"
            >
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] ${
                  s.cited ? 'bg-cyan-500/15 text-cyan-300' : 'bg-slate-800 text-slate-500'
                }`}
              >
                {s.source_id}
              </span>
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-200">{s.file_path}</span>
              <span className="hidden shrink-0 font-mono text-xs text-slate-500 sm:inline">{symbolLabel(s)}</span>
              <span className="shrink-0 font-mono text-xs text-slate-500">L{s.start_line}–{s.end_line}</span>
              {s.similarity != null && (
                <span className="shrink-0 font-mono text-xs text-slate-600">{s.similarity.toFixed(3)}</span>
              )}
              <span className={`w-12 shrink-0 text-right text-[11px] ${s.cited ? 'text-cyan-400' : 'text-slate-600'}`}>
                {s.cited ? 'cited' : '—'}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function EvidenceNotice({ evidence, citations }) {
  return (
    <>
      {evidence && evidence.level !== 'supported' && evidence.message && (
        <p
          data-testid="evidence-warning"
          className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
        >
          <span className="font-semibold">{evidence.level === 'weak' ? 'Weak evidence. ' : 'Unverified. '}</span>
          {evidence.message}
        </p>
      )}
      {citations?.invalid?.length > 0 && (
        <p data-testid="invalid-citations" className="text-xs text-slate-500">
          Removed {citations.invalid.length} citation{citations.invalid.length > 1 ? 's' : ''} to sources that do not
          exist ({citations.invalid.join(', ')}).
        </p>
      )}
    </>
  )
}

export function CitationChip({ id, source, onOpenSource }) {
  if (!source) return <span className="font-mono text-[11px] text-slate-500">{id}</span>
  return (
    <button
      type="button"
      onClick={() => onOpenSource(source)}
      title={`${source.file_path} L${source.start_line}–${source.end_line}`}
      className="mx-0.5 rounded bg-cyan-500/10 px-1 font-mono text-[11px] text-cyan-300 transition hover:bg-cyan-500/25"
    >
      {id}
    </button>
  )
}

export default function GroundedAnswer({ result, onOpenSource }) {
  const byId = Object.fromEntries((result.sources ?? []).map((s) => [s.source_id, s]))
  return (
    <div className="space-y-3">
      <Markdown
        text={result.answer}
        renderCitation={(id) => <CitationChip id={id} source={byId[id]} onOpenSource={onOpenSource} />}
      />
      {result.truncated && <p className="text-xs text-amber-300">The answer was cut off at the model's output limit.</p>}
      <EvidenceNotice evidence={result.evidence} citations={result.citations} />
      {result.message && (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          {result.message}
        </p>
      )}
      <SourcesPanel sources={result.sources} onOpenSource={onOpenSource} />
      <p className="font-mono text-[11px] text-slate-600">
        {result.model}
        {result.took_ms != null && ` · ${(result.took_ms / 1000).toFixed(1)}s`}
        {result.retrieval_ms != null && ` (retrieval ${result.retrieval_ms} ms)`}
        {result.usage?.input_tokens != null &&
          ` · ${result.usage.input_tokens.toLocaleString()} in / ${(result.usage.output_tokens ?? 0).toLocaleString()} out tokens`}
      </p>
    </div>
  )
}
