const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })

function Stat({ label, value, title }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/50 px-4 py-3" title={title}>
      <dt className="font-mono text-[11px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className="mt-1 truncate text-lg font-semibold text-slate-100">{value}</dd>
    </div>
  )
}

export default function RepositoryHeader({ repository, tree }) {
  const { owner, name, description, html_url, stars, forks, language, default_branch } = repository

  return (
    <section className="relative overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60 p-6 backdrop-blur">
      <div className="absolute -right-20 -top-20 h-48 w-48 rounded-full bg-cyan-500/10 blur-3xl pointer-events-none" />
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="font-mono text-sm text-slate-500">{owner} /</p>
            <span className="inline-flex items-center gap-1 rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-[10px] font-medium text-cyan-300">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-pulse" />
              AI Intelligence Ready
            </span>
          </div>
          <h2 className="truncate text-2xl font-bold tracking-tight text-slate-100">{name}</h2>
          <p className="mt-2 text-sm leading-relaxed text-slate-400">
            {description || <span className="italic text-slate-600">No description provided.</span>}
          </p>
        </div>
        <a
          href={html_url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 rounded-lg border border-slate-700 bg-slate-900/80 px-4 py-2 text-sm text-slate-300 transition hover:border-cyan-500/50 hover:text-cyan-300 hover:shadow-lg hover:shadow-cyan-500/10"
        >
          View on GitHub ↗
        </a>
      </div>

      <dl className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Stars" value={compact.format(stars)} title={stars.toLocaleString()} />
        <Stat label="Forks" value={compact.format(forks)} title={forks.toLocaleString()} />
        <Stat label="Language" value={language || '—'} />
        <Stat label="Branch" value={default_branch} />
        <Stat label="Files" value={tree.file_count.toLocaleString()} />
        <Stat label="Folders" value={tree.directory_count.toLocaleString()} />
      </dl>

      <p className="mt-4 truncate font-mono text-xs text-slate-600">{html_url}</p>
    </section>
  )
}
