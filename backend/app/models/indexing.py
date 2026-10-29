"""Request/response shapes for the database-backed (indexing) endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalyzeRequest(BaseModel):
    owner: str = Field(..., min_length=1, max_length=39)
    repo: str = Field(..., min_length=1, max_length=100)
    branch: str | None = Field(None, min_length=1, max_length=255)  # default: the repository's default branch


class AnalyzeResponse(BaseModel):
    repository_id: int
    repository: str
    branch: str
    commit_sha: str | None
    status: Literal["indexed", "unchanged"]  # "unchanged": nothing had to be written
    total_files: int
    files_processed: int
    files_skipped: int
    truncated: bool  # MAX_FILES_PER_ANALYSIS was reached
    languages: list[str]  # most files first
    language_files: dict[str, int]
    chunk_types: dict[str, int]
    chunks_created: int  # inserted by this run
    chunks_deleted: int  # removed by this run (changed or deleted files)
    chunks_total: int  # stored for the repository after this run
    files_added: int
    files_updated: int
    files_removed: int
    files_unchanged: int
    indexed_at: datetime
    duration_ms: int


class IndexStatus(BaseModel):
    indexed: bool
    repository_id: int | None = None
    repository: str | None = None
    indexed_at: datetime | None = None
    indexed_branch: str | None = None
    commit_sha: str | None = None
    files_processed: int | None = None
    chunks_total: int | None = None
    languages: list[str] = []


class StoredChunk(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chunk_key: str
    file_path: str
    file_hash: str
    language: str
    chunk_type: str
    symbol_name: str | None
    parent_symbol: str | None
    start_line: int
    end_line: int
    part: int | None
    total_parts: int | None
    content: str
    created_at: datetime
    updated_at: datetime


class StoredChunkPage(BaseModel):
    repository_id: int
    repository: str
    total: int
    page: int
    page_size: int
    total_pages: int
    chunks: list[StoredChunk]
