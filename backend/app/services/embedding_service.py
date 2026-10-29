"""Generate and store embeddings for a repository's code chunks.

Flow per batch:  pending chunks (DB) -> embedding text -> provider -> vectors -> DB (commit)

- Only chunks without an embedding from the current model are processed, so calling
  again never re-embeds finished work, and switching models re-embeds automatically.
- Each batch is committed on its own: a failure later on never loses earlier batches.
- One call works for about EMBEDDING_REQUEST_BUDGET_SECONDS and then returns its
  progress; calling again resumes where it stopped (no background jobs needed).
"""

import asyncio
import time

from app import config
from app.db import database, store
from app.db.database import DatabaseError
from app.services.embeddings import EmbeddingError, EmbeddingProvider, get_provider, provider_configured, with_retries

_running: set[int] = set()  # repositories being embedded by this process


def prepare_text(chunk) -> str:
    """Text sent to the embedding model: a short header for context, then the code.

    File path and symbol names carry a lot of meaning ("auth/session.py", "SessionStore.refresh")
    that a query like "where are sessions refreshed?" should match. Line numbers, hashes
    and ids are left out: they carry no meaning and would only add noise.
    """
    header = [f"File: {chunk.file_path}", f"Language: {chunk.language}"]
    if chunk.symbol_name:
        qualified = f"{chunk.parent_symbol}.{chunk.symbol_name}" if chunk.parent_symbol else chunk.symbol_name
        header.append(f"Symbol: {chunk.chunk_type} {qualified}")
    else:
        header.append(f"Type: {chunk.chunk_type}")
    if chunk.part:
        header.append(f"Part {chunk.part} of {chunk.total_parts}")
    return "\n".join(header) + "\n\n" + chunk.content


def embedding_status(repository_id: int) -> dict:
    with database.session_scope() as session:
        if store.get_repository(session, repository_id) is None:
            raise DatabaseError(404, "repository_not_indexed", f"No stored repository with id {repository_id}.")
        counts = store.embedding_counts(session, repository_id, config.EMBEDDING_MODEL)
    total, embedded, skipped = counts["total"], counts["embedded"], counts["skipped"]
    done = embedded + skipped
    return {
        "repository_id": repository_id,
        "provider": config.EMBEDDING_PROVIDER,
        "model": config.EMBEDDING_MODEL,
        "dimensions": config.EMBEDDING_DIMENSIONS,
        "provider_configured": provider_configured(),
        "total_chunks": total,
        "embedded_chunks": embedded,
        "skipped_chunks": skipped,  # empty chunks: nothing to embed
        "remaining_chunks": total - done,
        "outdated_chunks": counts["outdated"],  # embedded by a different model; will be redone
        "progress_percent": round(100 * done / total, 2) if total else 0.0,
        "last_embedded_at": counts["last_embedded_at"],
    }


def check_schema() -> None:
    with database.session_scope() as session:
        column = store.embedding_column_dimensions(session)
    if column is None:
        raise DatabaseError(503, "database_not_migrated", "The embedding column is missing. Run: alembic upgrade head")
    if column != config.EMBEDDING_DIMENSIONS:
        raise EmbeddingError(500, "embedding_dimension_mismatch",
                             f"EMBEDDING_DIMENSIONS is {config.EMBEDDING_DIMENSIONS} but the database column is "
                             f"vector({column}). Changing the size needs a new migration.")


async def embed_repository(repository_id: int, provider: EmbeddingProvider | None = None) -> dict:
    started = time.monotonic()
    deadline = started + config.EMBEDDING_REQUEST_BUDGET_SECONDS
    before = await asyncio.to_thread(embedding_status, repository_id)  # 404 if unknown
    await asyncio.to_thread(check_schema)
    provider = provider or get_provider()  # clear error if no API key
    batch_size = min(config.EMBEDDING_BATCH_SIZE, provider.max_batch_size)

    if repository_id in _running:
        raise EmbeddingError(409, "embedding_in_progress", "Embeddings are already being generated for this repository.")
    _running.add(repository_id)
    newly_embedded = failed = batches = 0
    error: EmbeddingError | None = None
    try:
        while True:
            chunks = await asyncio.to_thread(_pending, repository_id, provider.model, batch_size)
            if not chunks:
                break
            empty = [(c.id, None) for c in chunks if not c.content.strip()]
            work = [c for c in chunks if c.content.strip()]
            if empty:
                await asyncio.to_thread(_save, empty, provider.model)
            if work:
                try:
                    vectors = await _embed_with_retries(provider, [prepare_text(c) for c in work], deadline)
                except EmbeddingError as exc:
                    if newly_embedded == 0 and not exc.retryable:
                        raise  # nothing done in this call and retrying will not help: plain error response
                    error, failed = exc, len(work)
                    break
                newly_embedded += await asyncio.to_thread(_save, list(zip((c.id for c in work), vectors)), provider.model)
            batches += 1
            if time.monotonic() >= deadline:
                break
    finally:
        _running.discard(repository_id)

    if error is not None and newly_embedded == 0:
        raise error  # e.g. rate limited before anything succeeded; carries retry_after

    status = await asyncio.to_thread(embedding_status, repository_id)
    run_status = "complete" if status["remaining_chunks"] == 0 else ("interrupted" if error else "in_progress")
    return {
        **status,
        "status": run_status,
        "newly_embedded": newly_embedded,
        "already_embedded": before["embedded_chunks"],
        "failed_chunks": failed,
        "batches": batches,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "error": None if error is None else {
            "code": error.code, "message": error.message, "retry_after_seconds": error.retry_after,
        },
    }


async def embed_query(text: str, provider: EmbeddingProvider | None = None) -> list[float]:
    """Embed a search question with the configured provider (same model as the stored chunks)."""
    provider = provider or get_provider()
    deadline = time.monotonic() + config.EMBEDDING_REQUEST_BUDGET_SECONDS
    return await _with_retries(lambda: provider.embed_query(text), deadline)


async def _embed_with_retries(provider: EmbeddingProvider, texts: list[str], deadline: float) -> list[list[float]]:
    return await _with_retries(lambda: provider.embed_documents(texts), deadline)


async def _with_retries(call, deadline: float):
    return await with_retries(call, deadline, config.EMBEDDING_MAX_RETRIES)


def _pending(repository_id: int, model: str, limit: int) -> list:
    with database.session_scope() as session:
        return store.pending_chunks(session, repository_id, model, limit)


def _save(items: list, model: str) -> int:
    with database.session_scope() as session:  # one transaction (commit) per batch
        return store.save_embeddings(session, items, model)
