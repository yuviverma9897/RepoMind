"""Request/response shapes for semantic search."""

from pydantic import BaseModel, Field



class SearchRequest(BaseModel):
    # Empty queries, query length (1000) and top_k (1-50) are checked by the search service,
    # so clients get a clear {detail, code} error. This cap only rejects absurd request bodies.
    query: str = Field(max_length=10_000)
    top_k: int = 8


class SearchResult(BaseModel):
    rank: int
    chunk_id: int
    repository_id: int
    file_path: str
    language: str
    chunk_type: str
    symbol_name: str | None
    parent_symbol: str | None
    start_line: int
    end_line: int
    part: int | None
    total_parts: int | None
    similarity: float  # cosine similarity, 1 = same direction; higher is more relevant
    content: str


class SearchResponse(BaseModel):
    repository_id: int
    query: str
    top_k: int
    model: str
    metric: str
    total_chunks: int
    searched_chunks: int  # chunks with an embedding from the current model
    partial: bool  # some chunks are not embedded yet
    message: str | None
    took_ms: int
    results: list[SearchResult]
