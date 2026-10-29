"""Request/response shapes for security analysis and debug investigation (potential findings only)."""

from typing import Any

from pydantic import BaseModel, Field

from app.models.rag import Citations, Evidence, Source, TokenUsage


class AIReview(BaseModel):
    verdict: str  # likely_issue | likely_false_positive | uncertain
    reasoning: str


class Finding(BaseModel):
    id: str  # F1, F2, ... (most severe first)
    rule_id: str | None  # deterministic rule that produced it; None for AI findings
    title: str
    category: str
    severity: str  # critical | high | medium | low | info
    confidence: str  # high | medium | low
    file_path: str
    start_line: int
    end_line: int
    snippet: str | None  # secrets masked
    explanation: str
    reasoning: str | None
    recommendation: str
    requires_review: bool  # always true: potential finding, not a confirmed bug/vulnerability
    origin: str  # rule | ai
    source_id: str | None
    non_production: bool  # in tests, examples, docs or build/CI tooling (severity lowered one level)
    ai_review: AIReview | None


class SecurityRequest(BaseModel):
    path_prefix: str | None = Field(None, max_length=1000)
    use_ai: bool = False  # deterministic scan only unless asked (one LLM call when true)


class SecurityAI(BaseModel):
    status: str  # not_requested | ok | unparseable
    reviewed: int
    new_findings: int
    rejected: int  # AI findings dropped (unknown source, lines outside the evidence, invalid labels)
    model: str | None
    usage: TokenUsage | None
    sources: list[Source]


class SecurityResponse(BaseModel):
    repository_id: int
    path_prefix: str | None
    findings: list[Finding]
    total_findings: int
    truncated: bool
    counts: dict[str, dict[str, int]]
    files_scanned: int
    rule_languages: dict[str, int]
    secrets_only_languages: dict[str, int]
    ai: SecurityAI
    limitations: list[str]
    took_ms: int


class DebugRequest(BaseModel):
    description: str = Field("", max_length=10_000)  # optional when code is selected; max 1000 chars
    file_path: str | None = Field(None, max_length=1000)  # optional code to pin as [S1]
    symbol: str | None = Field(None, max_length=300)  # function/class in file_path (instead of lines)
    parent: str | None = Field(None, max_length=300)  # class of a method
    start_line: int | None = None
    end_line: int | None = None
    top_k: int = 8
    use_ai: bool = True


class DebugAI(BaseModel):
    status: str  # not_requested | ok | unparseable | no_evidence
    new_findings: int
    rejected: int


class DebugResponse(BaseModel):
    repository_id: int
    description: str
    summary: str | None  # Markdown citing [S#]; invalid citations removed
    findings: list[Finding]
    sources: list[Source]
    citations: Citations
    evidence: Evidence
    model: str | None
    usage: TokenUsage | None
    finish_reason: str | None
    truncated: bool
    ai: DebugAI
    partial: bool
    message: str | None
    limitations: list[str]
    took_ms: int
