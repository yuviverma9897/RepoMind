"""Response shapes for the embedding endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class EmbeddingStatus(BaseModel):
    repository_id: int
    provider: str
    model: str
    dimensions: int
    provider_configured: bool  # an API key is set (the key itself is never returned)
    total_chunks: int
    embedded_chunks: int
    skipped_chunks: int  # empty chunks: nothing to embed
    remaining_chunks: int
    outdated_chunks: int  # embedded by another model; will be re-embedded
    progress_percent: float
    last_embedded_at: datetime | None


class EmbeddingRunError(BaseModel):
    code: str
    message: str
    retry_after_seconds: float | None


class EmbeddingRunResponse(EmbeddingStatus):
    # complete: nothing left | in_progress: time budget used, call again | interrupted: see error, call again to resume
    status: Literal["complete", "in_progress", "interrupted"]
    newly_embedded: int  # by this call
    already_embedded: int  # before this call (not regenerated)
    failed_chunks: int  # in the batch that failed (still pending, retried next call)
    batches: int
    duration_ms: int
    error: EmbeddingRunError | None
