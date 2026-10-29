import { useEffect, useState } from 'react'
import { generateDocSection, getDocSections, getDocumentation } from '../api'
import GroundedAnswer from './GroundedAnswer'

const btn = 'rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-cyan-500/50 disabled:opacity-50'

function errorText(err) {
  return err.retryAfterSeconds ? `${err.message} Try again in about ${Math.ceil(err.retryAfterSeconds)} seconds.` : err.message
}

function Facts({ facts }) {
  const entries = Object.entries(facts ?? {}).filter(([, v]) => v != null && !(Array.isArray(v) && !v.length))
  if (!entries.length) return null
  return (
    <details className="rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs">
      <summary className="cursor-pointer font-mono uppercase tracking-wider text-slate-500">
        Static analysis facts given to the model
      </summary>
      <dl className="mt-2 space-y-1.5">
        {entries.map(([k, v]) => (
          <div key={k}>
            <dt className="font-mono text-slate-500">{k.replaceAll('_', ' ')}</dt>
            <dd className="break-words font-mono text-slate-300">
              {Array.isArray(v) ? v.join(' · ') : typeof v === 'object' ? Object.entries(v).map(([a, b]) => `${a} ${b}`).join(' · ') : String(v)}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

export default function DocumentationPanel({ repositoryId, onOpenSource }) {
  const [catalog, setCatalog] = useState([])
  const [doc, setDoc] = useState(null) // sections already generated (loaded, never generated, on mount)
  const [busy, setBusy] = useState(null) // section id being generated
  const [errors, setErrors] = useState({})
  const [loadError, setLoadError] = useState(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let alive = true
    Promise.all([getDocSections(), getDocumentation(repositoryId)])
      .then(([c, d]) => { if (alive) { setCatalog(c); setDoc(d) } })
      .catch((err) => alive && setLoadError(err.message))
    return () => { alive = false }
  }, [repositoryId])

  const done = Object.fromEntries((doc?.sections ?? []).map((s) => [s.section, s]))

  async function generate(id, regenerate = false) {
    setBusy(id)
    setErrors((e) => ({ ...e, [id]: null }))
    try {
      await generateDocSection(repositoryId, id, regenerate)
      setDoc(await getDocumentation(repositoryId))
      return true
    } catch (err) {
      setErrors((e) => ({ ...e, [id]: errorText(err) }))
      return false
    } finally {
      setBusy(null)
    }
  }

  async function generateMissing() {
    for (const s of catalog) {
      if (done[s.id]) continue
      if (!(await generate(s.id))) break // stop at the first failure (e.g. rate limit) instead of hammering the API
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(doc.markdown)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setLoadError('Copy failed: the browser blocked clipboard access. Use Download instead.')
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([doc.markdown], { type: 'text/markdown' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: `${doc.repository.replace('/', '-')}-docs.md` })
    a.click()
    URL.revokeObjectURL(url)
  }

  const missing = catalog.filter((s) => !done[s.id]).length
  return (
    <section className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4" data-testid="documentation-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-3xl">
          <h3 className="text-sm font-semibold text-slate-200">Documentation</h3>
          <p className="mt-0.5 text-sm text-slate-500">
            AI-assisted documentation written only from retrieved evidence: README parts, entry points, routes, manifests
            and the most imported modules. Each section is one bounded model call with file and line references; gaps are
            reported as <span className="text-slate-300">not found</span> rather than filled in. Nothing is generated until you ask.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={btn} disabled={!!busy || !catalog.length || !missing} onClick={generateMissing}>
            {missing === catalog.length ? 'Generate all sections' : `Generate remaining (${missing})`}
          </button>
          <button type="button" className={btn} disabled={!doc?.sections.length} onClick={copy}>{copied ? 'Copied' : 'Copy Markdown'}</button>
          <button type="button" className={btn} disabled={!doc?.sections.length} onClick={download}>Download .md</button>
        </div>
      </div>
      {loadError && <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{loadError}</p>}
      {!doc && !loadError && <p className="text-sm text-slate-500">Loading documentation sections…</p>}

      <ol className="space-y-3">
        {catalog.map((s) => {
          const result = done[s.id]
          return (
            <li key={s.id} className="rounded-lg border border-slate-800 bg-slate-950/30 px-4 py-3" data-testid={`doc-${s.id}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h4 className="text-sm font-medium text-slate-200">{s.title}</h4>
                  <p className="text-xs text-slate-500">{s.description}</p>
                </div>
                <div className="flex items-center gap-2">
                  {result && (
                    <span className="font-mono text-[11px] text-slate-500">
                      {result.status === 'insufficient_evidence' ? 'insufficient evidence' : 'generated'} ·{' '}
                      {new Date(result.generated_at).toLocaleString()}
                    </span>
                  )}
                  <button type="button" className={btn} disabled={!!busy} onClick={() => generate(s.id, !!result)} data-testid="doc-generate">
                    {busy === s.id ? 'Generating…' : result ? 'Regenerate' : 'Generate'}
                  </button>
                </div>
              </div>
              {errors[s.id] && (
                <p className="mt-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{errors[s.id]}</p>
              )}
              {result && (
                <div className="mt-3 space-y-3 border-t border-slate-800 pt-3">
                  {result.notes.length > 0 && (
                    <ul className="space-y-1 text-xs text-amber-200/90">
                      {result.notes.map((n) => <li key={n}>• {n}</li>)}
                    </ul>
                  )}
                  <GroundedAnswer result={{ ...result, answer: result.content, message: null }} onOpenSource={onOpenSource} />
                  <Facts facts={result.facts} />
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
