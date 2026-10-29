// Empty base URL = relative requests, handled by the Vite dev proxy.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

const BACKEND_UNAVAILABLE =
  'Cannot reach the RepoMind backend. Make sure it is running on port 8000.'

// Shared fetch wrapper: returns parsed JSON or throws an Error with a readable message.
async function request(path, options) {
  let res
  try {
    res = await fetch(`${API_BASE_URL}${path}`, options)
  } catch {
    throw new Error(BACKEND_UNAVAILABLE) // network error / CORS / server down
  }

  let data = null
  try {
    data = await res.json()
  } catch {
    // Non-JSON body: typically the dev proxy failing because the backend is down.
  }

  if (!res.ok) {
    const error = new Error(errorMessage(res.status, data))
    error.status = res.status
    error.code = data?.code
    error.retryAfterSeconds = data?.retry_after_seconds ?? null
    throw error
  }
  if (!data) throw new Error('The backend returned an invalid response.')
  return data
}

function errorMessage(status, data) {
  if (!data) return BACKEND_UNAVAILABLE
  // FastAPI validation errors (422) return a list of issues.
  if (Array.isArray(data.detail)) return 'Invalid request. Please check the input and try again.'
  return data.detail || `Request failed (${status}).`
}

export function getHealth() {
  return request('/api/health')
}

export function importRepository(url) {
  return request('/api/repos/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
}

export function getFileContent({ owner, repo, branch, path }) {
  const params = new URLSearchParams({ owner, repo, branch, path })
  return request(`/api/repos/file?${params}`)
}

export function analyzeCodeStructure({ owner, repo, branch, offset = 0, limit = 20, filePath = null, includeFiles = true }) {
  return request('/api/repos/chunks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      owner,
      repo,
      branch,
      offset,
      limit,
      file_path: filePath,
      include_files: includeFiles,
    }),
  })
}

export function analyzeAndIndex({ owner, repo, branch }) {
  return request('/api/repos/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ owner, repo, branch }),
  })
}

export function getIndexStatus({ owner, repo }) {
  const params = new URLSearchParams({ owner, repo })
  return request(`/api/repos/index-status?${params}`)
}

export function storedChunksUrl(repositoryId, page = 1, pageSize = 20) {
  return `${API_BASE_URL}/api/repos/${repositoryId}/chunks?page=${page}&page_size=${pageSize}`
}

// One call embeds for up to ~15 s and returns progress; call again until status is "complete".
export function generateEmbeddings(repositoryId) {
  return request(`/api/repos/${repositoryId}/embeddings`, { method: 'POST' })
}

export function getEmbeddingStatus(repositoryId) {
  return request(`/api/repos/${repositoryId}/embedding-status`)
}

// Semantic search over the repository's embedded chunks (top_k 1-50).
export function searchRepository(repositoryId, query, topK = 8) {
  return request(`/api/repos/${repositoryId}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_k: topK }),
  })
}

// Repository Q&A (RAG): answer generated from retrieved chunks, with the sources used (top_k 1-20).
// focus: optional { file_path, start_line, end_line } pinned as [S1] ("Ask about this code").
export function askRepository(repositoryId, question, topK = 8, focus = null) {
  return postJson(`/api/repos/${repositoryId}/ask`, { question, top_k: topK, focus })
}

function postJson(path, body) {
  return request(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
}

// Code intelligence (Phase 10)
export function getSymbols(repositoryId, filePath) {
  return request(`/api/repos/${repositoryId}/symbols?file_path=${encodeURIComponent(filePath)}`)
}

// body: { file_path, symbol?, parent?, start_line?, end_line? }
export function explainCode(repositoryId, body) {
  return postJson(`/api/repos/${repositoryId}/explain`, body)
}

// Architecture, dependencies and impact (Phase 11)
export function getDependencyGraph(repositoryId, { level = 'directory', depth = 2, pathPrefix = '', maxNodes = 80, includeExternal = false } = {}) {
  const params = new URLSearchParams({ level, depth, max_nodes: maxNodes, include_external: includeExternal })
  if (pathPrefix) params.set('path_prefix', pathPrefix)
  return request(`/api/repos/${repositoryId}/graph?${params}`)
}

export function summarizeArchitecture(repositoryId, pathPrefix = null) {
  return postJson(`/api/repos/${repositoryId}/architecture/summary`, { path_prefix: pathPrefix || null })
}

// body: { file_path, symbol?, depth? }
export function analyzeImpact(repositoryId, body) {
  return postJson(`/api/repos/${repositoryId}/impact`, body)
}

export function explainImpact(repositoryId, body) {
  return postJson(`/api/repos/${repositoryId}/impact/explain`, body)
}

// Debug and security (Phase 12): potential findings only; secrets are masked by the backend.
// body: { path_prefix?, use_ai? }
export function securityScan(repositoryId, body) {
  return postJson(`/api/repos/${repositoryId}/security`, body)
}

// body: { description, file_path?, start_line?, end_line?, use_ai? }
export function debugInvestigate(repositoryId, body) {
  return postJson(`/api/repos/${repositoryId}/debug`, body)
}

// Documentation (Phase 13). GET never generates; POST makes one bounded LLM call per section (cached server-side).
export function getDocSections() {
  return request('/api/repos/docs/sections')
}

export function getDocumentation(repositoryId) {
  return request(`/api/repos/${repositoryId}/docs`)
}

export function generateDocSection(repositoryId, section, regenerate = false) {
  return postJson(`/api/repos/${repositoryId}/docs/${section}`, { regenerate })
}

// Multi-agent orchestration (Phase 14): the backend routes the request to one specialist agent.
export function runAgent(repositoryId, query) {
  return postJson(`/api/repos/${repositoryId}/agent`, { query })
}

export function getAgents() {
  return request('/api/agents')
}
