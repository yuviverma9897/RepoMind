"""Code chunk model and the /api/repos/chunks request/response shapes.

CodeChunk maps 1:1 onto a row of the code_chunks table (app/db/models.py).
"""

from pydantic import BaseModel, Field


class CodeChunk(BaseModel):
    chunk_id: str  # deterministic: same repo/path/lines/content -> same id
    repository: str  # "owner/repo"
    file_path: str
    language: str
    chunk_type: str  # function | method | class | interface | struct | enum | trait | impl |
    #                  namespace | type | imports | module | section | block
    symbol_name: str | None = None  # e.g. "login"
    parent_symbol: str | None = None  # e.g. the class that owns a method
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    part: int | None = None  # set when one symbol was split into several chunks
    total_parts: int | None = None
    content: str


class FileSummary(BaseModel):
    path: str
    language: str
    parser: str  # which parsing strategy produced the chunks
    line_count: int
    chunk_count: int
    note: str | None = None  # e.g. why a parser fell back to line windows
    file_hash: str  # sha256 of the file's bytes


class ChunkRequest(BaseModel):
    owner: str = Field(..., min_length=1, max_length=39)
    repo: str = Field(..., min_length=1, max_length=100)
    branch: str = Field(..., min_length=1, max_length=255)
    # Pagination over the (cached) analysis result.
    offset: int = Field(0, ge=0)
    limit: int = Field(50, ge=1, le=200)
    file_path: str | None = Field(None, max_length=1000)  # only chunks of this file
    include_files: bool = True  # per-file summaries can be large; skip when paging


class ChunkResponse(BaseModel):
    repository: str
    branch: str
    commit_sha: str | None
    total_files: int  # regular files in the repository
    processed_files: int  # files that were parsed and chunked
    skipped_files: dict[str, int]  # reason -> count
    truncated: bool  # MAX_FILES_PER_ANALYSIS was reached
    languages: dict[str, int]  # language -> processed file count
    chunk_types: dict[str, int]  # chunk_type -> count
    parsers: dict[str, int]  # parser -> file count
    total_chunks: int
    chunk_size: int
    chunk_overlap: int
    files: list[FileSummary] | None
    offset: int
    limit: int
    matched_chunks: int  # after the optional file_path filter
    chunks: list[CodeChunk]
