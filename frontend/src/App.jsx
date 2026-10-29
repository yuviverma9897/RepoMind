import { useEffect, useRef, useState } from 'react'
import { getFileContent, importRepository } from './api'
import BackendStatus from './components/BackendStatus'
import ChunkInspector from './components/ChunkInspector'
import ArchitecturePanel from './components/ArchitecturePanel'
import CodeIntelligencePanel from './components/CodeIntelligencePanel'
import ImpactPanel from './components/ImpactPanel'
import AgentsPanel from './components/AgentsPanel'
import DocumentationPanel from './components/DocumentationPanel'
import DebugPanel from './components/DebugPanel'
import SecurityPanel from './components/SecurityPanel'
import CopilotPanel from './components/CopilotPanel'
import IndexPanel from './components/IndexPanel'
import CodeViewer from './components/CodeViewer'
import FileTree from './components/FileTree'
import RepositoryHeader from './components/RepositoryHeader'
import RepoUrlForm from './components/RepoUrlForm'
import SearchPanel from './components/SearchPanel'

const FEATURES = [
  { title: 'Repository indexing', text: 'Import public repos and map every file.', available: true },
  { title: 'Semantic code search', text: 'Find code by meaning, not just keywords.', available: true },
  { title: 'Grounded Q&A', text: 'Answers backed by citations to real source files.', available: true },
]

// Dashboard sections.
const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'files', label: 'Files' },
  { id: 'search', label: 'Search' },
  { id: 'copilot', label: 'Copilot' },
  { id: 'architecture', label: 'Architecture' },
  { id: 'impact', label: 'Impact Analysis' },
  { id: 'debug', label: 'Debug' },
  { id: 'security', label: 'Security' },
  { id: 'documentation', label: 'Documentation' },
  { id: 'agents', label: 'Agents' },
]

function NeedsIndex() {
  return (
    <p className="rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4 text-sm text-slate-400">
      Analyze and index the repository in <span className="text-slate-200">Overview</span> first.
    </p>
  )
}

export default function App() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [indexStatus, setIndexStatus] = useState(null)
  const [tab, setTab] = useState('overview')
  const [agentRuns, setAgentRuns] = useState([]) // this session's agent activity (newest first, max 20)
  const [impactTarget, setImpactTarget] = useState(null) // { file_path } sent from the architecture view
  const [debugTarget, setDebugTarget] = useState(null) // { file_path, symbol?, parent? } sent from Files
  const [visited, setVisited] = useState(() => new Set(['overview'])) // heavy sections load on first visit
  useEffect(() => {
    setVisited((prev) => (prev.has(tab) ? prev : new Set(prev).add(tab)))
  }, [tab])

  // Code viewer state
  const [selectedPath, setSelectedPath] = useState(null)
  const [file, setFile] = useState(null)
  const [fileLoading, setFileLoading] = useState(false)
  const [fileError, setFileError] = useState(null)
  const [highlight, setHighlight] = useState(null) // { start, end } lines from a search result
  const fileCache = useRef(new Map()) // avoids re-fetching (and spending GitHub rate limit)
  const latestRequest = useRef(null) // ignores responses for files no longer selected

  function resetViewer() {
    setSelectedPath(null)
    setFile(null)
    setFileError(null)
    setFileLoading(false)
    setHighlight(null)
    fileCache.current.clear()
    latestRequest.current = null
  }

  async function handleAnalyze(url) {
    setLoading(true)
    setError(null)
    resetViewer()
    setIndexStatus(null)
    setTab('overview')
    setImpactTarget(null)
    setAgentRuns([])
    setVisited(new Set(['overview']))
    try {
      setResult(await importRepository(url))
    } catch (err) {
      setResult(null)
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function openFile(path, lines = null) {
    const { owner, name, default_branch } = result.repository
    const key = `${owner}/${name}@${default_branch}:${path}`

    setSelectedPath(path)
    setHighlight(lines)
    setFileError(null)
    latestRequest.current = key

    if (fileCache.current.has(key)) {
      setFile(fileCache.current.get(key))
      setFileLoading(false)
      return
    }

    setFile(null)
    setFileLoading(true)
    try {
      const data = await getFileContent({ owner, repo: name, branch: default_branch, path })
      fileCache.current.set(key, data)
      if (latestRequest.current === key) setFile(data)
    } catch (err) {
      if (latestRequest.current === key) setFileError(err.message)
    } finally {
      if (latestRequest.current === key) setFileLoading(false)
    }
  }

  // Open a file at a chunk's lines (search results, sources, findings): switches to the Files section.
  function openAtLines(chunk) {
    setTab('files')
    openFile(chunk.file_path, chunk.start_line ? { start: chunk.start_line, end: chunk.end_line } : null)
    requestAnimationFrame(() => document.getElementById('code-viewer')?.scrollIntoView({ behavior: 'smooth' }))
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-slate-950 font-sans text-slate-100">
      {/* Subtle grid + glow background */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            'linear-gradient(#94a3b8 1px, transparent 1px), linear-gradient(90deg, #94a3b8 1px, transparent 1px)',
          backgroundSize: '48px 48px',
        }}
      />
      <div className="pointer-events-none absolute -top-40 left-1/2 h-96 w-[48rem] -translate-x-1/2 rounded-full bg-cyan-500/10 blur-3xl" />

      <header className="relative mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <span className="text-lg font-semibold tracking-tight">RepoMind</span>
        </div>
        <BackendStatus />
      </header>

      <main
        className={`relative mx-auto flex max-w-3xl flex-col items-center px-6 pb-24 text-center ${
          result ? 'pt-10' : 'pt-20 sm:pt-28'
        }`}
      >
        {!result && (
          <span className="mb-6 rounded-full border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 font-mono text-xs text-cyan-300">
            Early development · v0.12
          </span>
        )}

        <h1 className={`font-bold tracking-tight ${result ? 'text-3xl' : 'text-5xl sm:text-6xl'}`}>
          Repo<span className="bg-gradient-to-r from-cyan-400 to-violet-400 bg-clip-text text-transparent">Mind</span>
        </h1>

        {!result && (
          <p className="mt-5 max-w-xl text-lg leading-relaxed text-slate-400">
            AI-powered intelligence for GitHub repositories. Understand architecture,
            search code semantically, and ask questions grounded in the actual source.
          </p>
        )}

        <div className={`w-full ${result ? 'mt-6' : 'mt-10'}`}>
          <RepoUrlForm onAnalyze={handleAnalyze} loading={loading} error={error} />
        </div>

        {!result && !loading && (
          <section className="mt-20 grid w-full gap-4 text-left sm:grid-cols-3">
            {FEATURES.map((item) => (
              <div key={item.title} className="rounded-xl border border-slate-800/80 bg-slate-900/40 p-5">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-slate-200">{item.title}</h3>
                  <span className={`font-mono text-[10px] uppercase tracking-wider ${item.available ? 'text-emerald-400' : 'text-slate-500'}`}>
                    {item.available ? 'Available' : 'Planned'}
                  </span>
                </div>
                <p className="mt-2 text-sm text-slate-500">{item.text}</p>
              </div>
            ))}
          </section>
        )}
      </main>

      {result && (
        <div className="relative mx-auto -mt-16 flex max-w-7xl flex-col gap-6 px-6 pb-24 text-left">
          <RepositoryHeader repository={result.repository} tree={result.tree} />
          <nav
            role="tablist"
            aria-label="Repository sections"
            className="sticky top-0 z-20 -mx-1 flex gap-1 overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/90 p-1 backdrop-blur"
          >
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                type="button"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`shrink-0 rounded-lg px-3.5 py-1.5 text-sm transition ${
                  tab === t.id ? 'bg-slate-800 font-medium text-cyan-300' : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>

          {/* Every section stays mounted (hidden) so switching tabs keeps its state. */}
          <div hidden={tab !== 'overview'} className="space-y-6">
            <IndexPanel
              key={`index:${result.repository.full_name}`}
              repository={result.repository}
              onStatusChange={setIndexStatus}
            />
          </div>

          <div hidden={tab !== 'files'} className="space-y-6">
            <div className="grid gap-6 lg:h-[75vh] lg:grid-cols-[320px_minmax(0,1fr)]">
              <div className="flex h-[50vh] min-h-0 flex-col lg:h-auto">
                <FileTree
                  files={result.files}
                  tree={result.tree}
                  selectedPath={selectedPath}
                  onSelectFile={(path) => openFile(path)}
                />
              </div>
              <div id="code-viewer" className="flex h-[70vh] min-h-0 scroll-mt-16 flex-col lg:h-auto">
                <CodeViewer
                  selectedPath={selectedPath}
                  file={file}
                  loading={fileLoading}
                  error={fileError}
                  highlight={highlight}
                  onRetry={() => selectedPath && openFile(selectedPath, highlight)}
                />
              </div>
            </div>
            {indexStatus?.indexed ? (
              <CodeIntelligencePanel
                key={`intel:${result.repository.full_name}`}
                repositoryId={indexStatus.repository_id}
                selectedPath={selectedPath}
                highlight={highlight}
                onOpenSource={openAtLines}
                onShowLines={(start, end, whole) => selectedPath && openFile(selectedPath, whole ? null : { start, end })}
                onInvestigate={(t) => {
                  setDebugTarget({ ...t, nonce: Date.now() })
                  setTab('debug')
                }}
              />
            ) : (
              selectedPath && <NeedsIndex />
            )}
            <ChunkInspector
              key={`chunks:${result.repository.full_name}`}
              repository={result.repository}
              onOpenFile={(path) => openAtLines({ file_path: path })}
            />
          </div>

          <div hidden={tab !== 'search'}>
            {indexStatus?.indexed ? (
              <SearchPanel
                key={`search:${result.repository.full_name}`}
                repositoryId={indexStatus.repository_id}
                onOpenResult={openAtLines}
              />
            ) : (
              <NeedsIndex />
            )}
          </div>

          <div hidden={tab !== 'architecture'}>
            {indexStatus?.indexed ? (
              visited.has('architecture') ? (
                <ArchitecturePanel
                  key={`arch:${result.repository.full_name}`}
                  repositoryId={indexStatus.repository_id}
                  onOpenFile={(path) => openAtLines({ file_path: path })}
                  onOpenSource={openAtLines}
                  onAnalyzeImpact={(path) => {
                    setImpactTarget({ file_path: path })
                    setTab('impact')
                  }}
                />
              ) : null
            ) : (
              <NeedsIndex />
            )}
          </div>

          <div hidden={tab !== 'impact'}>
            {indexStatus?.indexed ? (
              <ImpactPanel
                key={`impact:${result.repository.full_name}`}
                repositoryId={indexStatus.repository_id}
                files={result.files}
                target={impactTarget}
                onOpenSource={openAtLines}
              />
            ) : (
              <NeedsIndex />
            )}
          </div>

          <div hidden={tab !== 'debug'}>
            {indexStatus?.indexed ? (
              <DebugPanel
                key={`debug:${result.repository.full_name}`}
                repositoryId={indexStatus.repository_id}
                files={result.files}
                selectedPath={selectedPath}
                target={debugTarget}
                onOpenSource={openAtLines}
              />
            ) : (
              <NeedsIndex />
            )}
          </div>

          <div hidden={tab !== 'security'}>
            {indexStatus?.indexed ? (
              <SecurityPanel
                key={`security:${result.repository.full_name}`}
                repositoryId={indexStatus.repository_id}
                onOpenSource={openAtLines}
              />
            ) : (
              <NeedsIndex />
            )}
          </div>

          <div hidden={tab !== 'documentation'}>
            {indexStatus?.indexed ? (
              <DocumentationPanel
                key={`docs:${result.repository.full_name}`}
                repositoryId={indexStatus.repository_id}
                onOpenSource={openAtLines}
              />
            ) : (
              <NeedsIndex />
            )}
          </div>

          <div hidden={tab !== 'agents'}>
            <AgentsPanel runs={agentRuns} onOpenCopilot={() => setTab('copilot')} />
          </div>

          <div hidden={tab !== 'copilot'}>
            {indexStatus?.indexed ? (
              <CopilotPanel
                key={`copilot:${result.repository.full_name}`}
                repositoryId={indexStatus.repository_id}
                onOpenSource={openAtLines}
                onAgentRun={(run) => setAgentRuns((prev) => [run, ...prev].slice(0, 20))}
              />
            ) : (
              <NeedsIndex />
            )}
          </div>
        </div>
      )}

      <footer className="relative border-t border-slate-900 py-6 text-center font-mono text-xs text-slate-600">
        RepoMind · Multi-agent code analysis platform
      </footer>
    </div>
  )
}
