"""Request/response shapes for code intelligence (explain file / symbol / selection)."""

from typing import Any

from pydantic import BaseModel, Field

from app.models.rag import Citations, Evidence, Source, TokenUsage


class Symbol(BaseModel):
    kind: str  # function | method | class | struct | interface | ...
    name: str
    parent: str | None
    start_line: int
    end_line: int


class SymbolList(BaseModel):
    repository_id: int
    file_path: str
    language: str
    line_count: int
    structure_support: str | None  # python-ast | heuristic | None (unsupported language)
    symbols: list[Symbol]


class ExplainRequest(BaseModel):
    file_path: str = Field(min_length=1, max_length=1000)
    symbol: str | None = Field(None, max_length=300)  # explain this function / method / class
    parent: str | None = Field(None, max_length=300)  # disambiguates methods: the owning class
    start_line: int | None = None  # or explain a selection
    end_line: int | None = None


class ExplainTarget(BaseModel):
    kind: str  # file | function | class | selection
    symbol_kind: str
    label: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    truncated: bool
    note: str | None


class ExplainResponse(BaseModel):
    repository_id: int
    repository: str
    target: ExplainTarget
    structure: dict[str, Any] | None  # deterministic facts (parameters, calls, ...)
    structure_note: str | None
    answer: str
    model: str
    finish_reason: str
    truncated: bool
    usage: TokenUsage
    sources: list[Source]
    citations: Citations
    evidence: Evidence
    took_ms: int
