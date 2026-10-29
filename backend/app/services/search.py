"""Semantic code search: question -> query embedding -> pgvector nearest chunks.

    API route -> search (this module) -> embedding_service.embed_query -> provider
                                      -> store.search_chunks -> PostgreSQL / pgvector

Results are ranked by meaning only (cosine similarity of embeddings). No LLM is involved:
the output is the evidence itself (file, symbol, lines, code), ready for later RAG use.
"""

import asyncio
import time

from app import config
from app.db import database, store
from app.services import embedding_service, secrets
from app.services.embeddings import EmbeddingProvider

MAX_QUERY_CHARS = 1000
MAX_TOP_K = 50


class SearchError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def search_repository(repository_id: int, query: str, top_k: int,
                            provider: EmbeddingProvider | None = None) -> dict:
    started = time.monotonic()
    query = validate_query(query, top_k, MAX_TOP_K)

    status = await asyncio.to_thread(embedding_service.embedding_status, repository_id)  # 404 if unknown
    if status["total_chunks"] == 0:
        raise SearchError(409, "repository_has_no_chunks",
                          "This repository has no stored code chunks. Analyze & index it first.")

    # If no chunks are embedded yet, attempt to embed the first batch immediately
    if status["embedded_chunks"] == 0:
        try:
            await embedding_service.embed_repository(repository_id, provider)
            status = await asyncio.to_thread(embedding_service.embedding_status, repository_id)
        except Exception:
            pass

    # If still 0 embedded chunks, fall back to token/keyword relevance so users & AI copilot are never blocked
    if status["embedded_chunks"] == 0:
        rows = await asyncio.to_thread(_fallback_token_search, repository_id, query, top_k)
        if rows:
            return {
                "repository_id": repository_id,
                "query": query,
                "top_k": top_k,
                "model": "keyword_fallback",
                "metric": "relevance",
                "total_chunks": status["total_chunks"],
                "searched_chunks": status["total_chunks"],
                "partial": True,
                "message": "Embeddings are currently generating in the background. Showing keyword matches.",
                "took_ms": round((time.monotonic() - started) * 1000),
                "results": [_result(rank, row) for rank, row in enumerate(rows, start=1)],
            }
        hint = " (existing embeddings were made with a different model)" if status["outdated_chunks"] else ""
        raise SearchError(409, "embeddings_not_ready",
                          f"Repository embeddings are generating. Click 'Generate Embeddings' or wait a moment.")

    await asyncio.to_thread(embedding_service.check_schema)

    vector = await embedding_service.embed_query(query, provider)
    rows = await asyncio.to_thread(_search, repository_id, vector, top_k)

    partial = status["remaining_chunks"] > 0
    message = None
    if not rows:
        message = "No matching code chunks were found."
    elif partial:
        message = (f"Only {status['embedded_chunks']:,} of {status['total_chunks']:,} chunks are embedded, "
                   "so results may miss relevant code. Finish generating embeddings for complete results.")

    return {
        "repository_id": repository_id,
        "query": query,
        "top_k": top_k,
        "model": config.EMBEDDING_MODEL,
        "metric": "cosine",
        "total_chunks": status["total_chunks"],
        "searched_chunks": status["embedded_chunks"],
        "partial": partial,
        "message": message,
        "took_ms": round((time.monotonic() - started) * 1000),
        "results": [_result(rank, row) for rank, row in enumerate(rows, start=1)],
    }


def validate_query(query: str, top_k: int, max_top_k: int) -> str:
    """Trimmed query, or a 400 SearchError for empty/too long queries and out-of-range top_k."""
    query = (query or "").strip()
    if not query:
        raise SearchError(400, "empty_query", "Enter a search query.")
    if len(query) > MAX_QUERY_CHARS:
        raise SearchError(400, "query_too_long", f"Search queries are limited to {MAX_QUERY_CHARS} characters.")
    if not 1 <= top_k <= max_top_k:
        raise SearchError(400, "invalid_top_k", f"top_k must be between 1 and {max_top_k}.")
    return query


def _search(repository_id: int, vector: list[float], top_k: int) -> list:
    with database.session_scope() as session:
        return store.search_chunks(session, repository_id, config.EMBEDDING_MODEL, vector, top_k)


def _result(rank: int, row) -> dict:
    return {
        "rank": rank,
        "chunk_id": row.id,
        "repository_id": row.repository_id,
        "file_path": row.file_path,
        "language": row.language,
        "chunk_type": row.chunk_type,
        "symbol_name": row.symbol_name,
        "parent_symbol": row.parent_symbol,
        "start_line": row.start_line,
        "end_line": row.end_line,
        "part": row.part,
        "total_parts": row.total_parts,
        "similarity": round(float(row.similarity), 4),
        "content": secrets.mask(row.content),  # detected secrets never leave the backend
    }


def _fallback_token_search(repository_id: int, query: str, limit: int) -> list:
    from collections import namedtuple
    import re
    tokens = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
    if not tokens:
        tokens = [t.lower() for t in query.split() if t]
    if not tokens:
        return []

    with database.session_scope() as session:
        chunks = list(store.list_chunks(session, repository_id, page=1, page_size=200))
        scored = []
        for c in chunks:
            score = 0.0
            content_lower = c.content.lower()
            path_lower = c.file_path.lower()
            sym_lower = (c.symbol_name or "").lower()
            for t in tokens:
                if t in sym_lower:
                    score += 3.0
                if t in path_lower:
                    score += 2.0
                if t in content_lower:
                    score += 1.0 + content_lower.count(t) * 0.1
            if score > 0:
                scored.append((c, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        ChunkRow = namedtuple("ChunkRow", [
            "id", "repository_id", "file_path", "language", "chunk_type",
            "symbol_name", "parent_symbol", "start_line", "end_line",
            "part", "total_parts", "content", "similarity"
        ])
        results = []
        for c, s in scored[:limit]:
            results.append(ChunkRow(
                id=c.id, repository_id=c.repository_id, file_path=c.file_path, language=c.language,
                chunk_type=c.chunk_type, symbol_name=c.symbol_name, parent_symbol=c.parent_symbol,
                start_line=c.start_line, end_line=c.end_line, part=c.part, total_parts=c.total_parts,
                content=c.content, similarity=min(0.95, round(s / 10.0, 4))
            ))
        return results
