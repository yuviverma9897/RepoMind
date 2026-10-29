"""Request/response shapes for repository Q&A (RAG)."""

from pydantic import BaseModel, Field

from app.models.search import SearchResult


class Focus(BaseModel):
    file_path: str = Field(min_length=1, max_length=1000)
    start_line: int | None = None  # both None = the whole file
    end_line: int | None = None


class AskRequest(BaseModel):
    # Empty questions, length (1000) and top_k (1-20) are checked by the service.
    question: str = Field(max_length=10_000)
    top_k: int = 8
    focus: Focus | None = None  # "Ask about this code": pinned as [S1]


class Source(SearchResult):
    source_id: str  # "S1", "S2", ... as cited in the answer
    cited: bool = False  # the answer cites this source
    # Sources not found by similarity search (pinned code, imports, definitions) have no rank/score.
    rank: int | None = None
    chunk_id: int | None = None
    similarity: float | None = None
    role: str | None = None  # focus | target | imports | definition | ...


class Citations(BaseModel):
    cited: list[str]  # valid source ids, in order of first use
    invalid: list[str]  # ids the model used that match no source; removed from the answer


class Evidence(BaseModel):
    level: str  # supported | weak | uncited | none
    best_similarity: float | None
    threshold: float
    message: str | None


class TokenUsage(BaseModel):
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None


class AskResponse(BaseModel):
    repository_id: int
    repository: str
    question: str
    answer: str  # Markdown-ish text citing sources as [S1]
    model: str
    finish_reason: str
    truncated: bool  # the model hit its output limit
    usage: TokenUsage
    sources: list[Source]  # exactly the chunks the model saw
    citations: Citations
    evidence: Evidence
    partial: bool
    message: str | None
    retrieval_ms: int
    took_ms: int
