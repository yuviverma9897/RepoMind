import asyncio

from fastapi import APIRouter, Query

from app.models.architecture import (GraphResponse, ImpactExplanation, ImpactRequest, ImpactResponse, SummaryRequest,
                                     SummaryResponse)
from app.models.chunk import ChunkRequest, ChunkResponse
from app.models.code_intelligence import ExplainRequest, ExplainResponse, SymbolList
from app.agents import orchestrator
from app.models.agents import AgentInfo, AgentRequest, AgentResponse
from app.models.documentation import DocSection, Documentation, SectionInfo, SectionRequest
from app.models.findings import DebugRequest, DebugResponse, SecurityRequest, SecurityResponse
from app.models.embedding import EmbeddingRunResponse, EmbeddingStatus
from app.models.indexing import AnalyzeRequest, AnalyzeResponse, IndexStatus, StoredChunk, StoredChunkPage
from app.models.rag import AskRequest, AskResponse
from app.models.repository import FileContentResponse, ImportRequest, ImportResponse
from app.models.search import SearchRequest, SearchResponse
from app.services.github import get_file_content, import_repository
from app.services import (architecture, code_intelligence, debug, documentation, dependencies, embedding_service, indexing, rag, search,
                          secrets, security)
from app.services.pipeline import analyze_repository

router = APIRouter(prefix="/api/repos", tags=["repositories"])


@router.post("/import", response_model=ImportResponse)
async def import_repo(payload: ImportRequest):
    """Fetch a public GitHub repository's metadata and file tree."""
    return await import_repository(payload.url)


@router.get("/file", response_model=FileContentResponse)
async def get_file(
    owner: str = Query(..., max_length=39),
    repo: str = Query(..., max_length=100),
    branch: str = Query(..., max_length=255),
    path: str = Query(..., max_length=1000),
):
    """Fetch the contents of a single file from a public GitHub repository (detected secrets masked)."""
    result = await get_file_content(owner, repo, branch, path)
    if result.get("content"):
        masked, found = secrets.mask_secrets(result["content"])
        result = {**result, "content": masked, "secrets_masked": len(found)}
    return result


@router.post("/chunks", response_model=ChunkResponse)
async def get_chunks(payload: ChunkRequest):
    """Parse and chunk a repository's source files, returning one page of chunks.

    The full analysis is cached briefly, so paging does not re-download the repository.
    """
    analysis = await analyze_repository(payload.owner, payload.repo, payload.branch)

    chunks = analysis["chunks"]
    if payload.file_path:
        chunks = [c for c in chunks if c.file_path == payload.file_path]
    # Copies with secrets masked: the cached analysis itself is never modified.
    page = [_masked_copy(c) for c in chunks[payload.offset : payload.offset + payload.limit]]

    summary = {k: v for k, v in analysis.items() if k not in ("chunks", "files")}
    return {
        **summary,
        "files": analysis["files"] if payload.include_files else None,
        "offset": payload.offset,
        "limit": payload.limit,
        "matched_chunks": len(chunks),
        "chunks": page,
    }


# ---------------------------------------------------------------- stored index (PostgreSQL)

@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_and_index(payload: AnalyzeRequest):
    """Analyze a repository and store its chunks. Re-running only rewrites changed files."""
    return await indexing.index_repository(payload.owner, payload.repo, payload.branch)


@router.get("/index-status", response_model=IndexStatus)
def get_index_status(
    owner: str = Query(..., min_length=1, max_length=39),
    repo: str = Query(..., min_length=1, max_length=100),
):
    """Whether a repository has been indexed (database only, no GitHub request)."""
    return indexing.index_status(owner, repo)


@router.get("/{repository_id}/chunks", response_model=StoredChunkPage)
def list_stored_chunks(
    repository_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    file_path: str | None = Query(None, max_length=1000),
):
    """One page of a repository's stored chunks, ordered by file and line (detected secrets masked)."""
    result = indexing.stored_chunks(repository_id, page, page_size, file_path)
    # Pydantic copies: the ORM rows are never modified, so masking can never be written back to the database.
    result["chunks"] = [_masked_copy(StoredChunk.model_validate(c)) for c in result["chunks"]]
    return result


def _masked_copy(chunk):
    masked = secrets.mask(chunk.content)
    return chunk if masked == chunk.content else chunk.model_copy(update={"content": masked})


# ---------------------------------------------------------------- embeddings (pgvector)

@router.post("/{repository_id}/embeddings", response_model=EmbeddingRunResponse)
async def generate_embeddings(repository_id: int):
    """Embed the repository's chunks that have no embedding yet, in batches.

    Works for about EMBEDDING_REQUEST_BUDGET_SECONDS per call; call again while status
    is "in_progress" or "interrupted". Existing embeddings are never regenerated.
    """
    return await embedding_service.embed_repository(repository_id)


@router.get("/{repository_id}/embedding-status", response_model=EmbeddingStatus)
def get_embedding_status(repository_id: int):
    return embedding_service.embedding_status(repository_id)


# ---------------------------------------------------------------- semantic search

@router.post("/{repository_id}/search", response_model=SearchResponse)
async def search_repository(repository_id: int, body: SearchRequest):
    """Natural-language search over this repository's embedded code chunks (top_k: 1-50)."""
    return await search.search_repository(repository_id, body.query, body.top_k)


# ---------------------------------------------------------------- repository Q&A (RAG)

@router.post("/{repository_id}/ask", response_model=AskResponse)
async def ask_repository(repository_id: int, body: AskRequest):
    """Answer a question using only retrieved code chunks (top_k: 1-20). Sources are returned with the answer."""
    focus = body.focus.model_dump() if body.focus else None
    return await rag.ask(repository_id, body.question, body.top_k, focus=focus)


@router.get("/{repository_id}/symbols", response_model=SymbolList)
async def list_symbols(repository_id: int, file_path: str = Query(..., min_length=1, max_length=1000)):
    """Functions, methods and classes of one stored file (from its chunks)."""
    return await asyncio.to_thread(code_intelligence.list_symbols, repository_id, file_path)


@router.post("/{repository_id}/explain", response_model=ExplainResponse)
async def explain_code(repository_id: int, body: ExplainRequest):
    """Explain a whole file, one symbol (symbol + optional parent) or a line selection (start_line/end_line)."""
    return await code_intelligence.explain(repository_id, body.file_path, symbol=body.symbol, parent=body.parent,
                                           start_line=body.start_line, end_line=body.end_line)


@router.get("/{repository_id}/graph", response_model=GraphResponse)
async def dependency_graph(
    repository_id: int,
    level: str = Query("directory", pattern="^(directory|file)$"),
    depth: int = Query(2, ge=1, le=6),
    path_prefix: str | None = Query(None, max_length=1000),
    max_nodes: int = Query(dependencies.DEFAULT_GRAPH_NODES, ge=1, le=dependencies.MAX_GRAPH_NODES),
    include_external: bool = False,
):
    """Import graph {nodes, edges}, aggregated by directory (depth = path segments) or per file, limited to max_nodes."""
    return await asyncio.to_thread(dependencies.graph_view, repository_id, level=level, depth=depth,
                                   path_prefix=path_prefix, max_nodes=max_nodes, include_external=include_external)


@router.post("/{repository_id}/architecture/summary", response_model=SummaryResponse)
async def architecture_summary(repository_id: int, body: SummaryRequest):
    """LLM architecture overview grounded in dependency facts, the README and the most-imported files."""
    return await architecture.summarize(repository_id, body.path_prefix)


@router.post("/{repository_id}/impact", response_model=ImpactResponse)
async def impact_analysis(repository_id: int, body: ImpactRequest):
    """Deterministic impact: importers of the file (transitively) and text references to the symbol. No LLM."""
    return await asyncio.to_thread(dependencies.impact, repository_id, body.file_path, body.symbol, body.depth)


@router.post("/{repository_id}/impact/explain", response_model=ImpactExplanation)
async def impact_explanation(repository_id: int, body: ImpactRequest):
    """AI-assisted impact assessment built on /impact. Not guaranteed to be complete."""
    return await architecture.explain_impact(repository_id, body.file_path, body.symbol, body.depth)


# ---------------------------------------------------------------- debug & security (potential findings only)

@router.post("/{repository_id}/security", response_model=SecurityResponse)
async def security_analysis(repository_id: int, body: SecurityRequest):
    """Pattern-based security scan (secrets masked); use_ai adds one LLM review call. Findings need human review."""
    return await security.analyze(repository_id, path_prefix=body.path_prefix, use_ai=body.use_ai)


@router.post("/{repository_id}/debug", response_model=DebugResponse)
async def debug_investigation(repository_id: int, body: DebugRequest):
    """Investigate a described problem: retrieved evidence, rule hints and (use_ai) AI-proposed causes. Never edits code."""
    return await debug.investigate(repository_id, body.description, file_path=body.file_path, symbol=body.symbol,
                                   parent=body.parent, start_line=body.start_line, end_line=body.end_line, top_k=body.top_k,
                                   use_ai=body.use_ai)


# ---------------------------------------------------------------- documentation (repository-grounded)

@router.get("/docs/sections", response_model=list[SectionInfo])
def documentation_sections():
    """The documentation sections RepoMind can generate."""
    return documentation.section_catalog()


@router.get("/{repository_id}/docs", response_model=Documentation)
async def get_documentation(repository_id: int):
    """Sections generated so far (never generates anything) and the assembled Markdown."""
    return await asyncio.to_thread(documentation.document, repository_id)


@router.post("/{repository_id}/docs/{section}", response_model=DocSection)
async def generate_documentation_section(repository_id: int, section: str, body: SectionRequest | None = None):
    """Generate one section from targeted evidence (one bounded LLM call; cached until re-indexing)."""
    return await documentation.generate_section(repository_id, section, regenerate=bool(body and body.regenerate))


# ---------------------------------------------------------------- multi-agent orchestration

@router.post("/{repository_id}/agent", response_model=AgentResponse)
async def run_agent(repository_id: int, body: AgentRequest):
    """Route a free-form request to one specialist agent; returns its answer, the routing decision and real activity."""
    return await orchestrator.run(repository_id, body.query)
