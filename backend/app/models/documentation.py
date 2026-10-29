"""Request/response shapes for repository-grounded documentation (Phase 13)."""

from typing import Any

from pydantic import BaseModel

from app.models.rag import Citations, Evidence, Source, TokenUsage


class SectionInfo(BaseModel):
    id: str
    title: str
    description: str


class SectionRequest(BaseModel):
    regenerate: bool = False  # ignore the cached result (one new LLM call)


class DocSection(BaseModel):
    repository_id: int
    repository: str
    section: str
    title: str
    status: str  # generated | insufficient_evidence
    content: str  # Markdown citing [S#]; invalid citations removed
    facts: dict[str, Any]  # deterministic analysis shown next to the AI text
    sources: list[Source]
    citations: Citations
    evidence: Evidence | None
    notes: list[str]  # what was missing or not used (no README, no routes, search unavailable...)
    model: str | None
    usage: TokenUsage | None
    finish_reason: str | None
    truncated: bool
    cached: bool
    generated_at: str
    took_ms: int


class Documentation(BaseModel):
    repository_id: int
    repository: str
    title: str
    sections: list[DocSection]  # generated so far, in documentation order
    available_sections: list[SectionInfo]
    markdown: str  # the assembled document for copy/download
