"""Request/response shapes for the multi-agent endpoint (Phase 14)."""

from typing import Any

from pydantic import BaseModel, Field

from app.models.rag import Source


class AgentInfo(BaseModel):
    name: str
    title: str
    description: str
    uses_llm: str  # never | optional | always


class AgentRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class Routing(BaseModel):
    agent: str | None  # None = unsupported request
    reason: str
    matched: list[str]  # the rule matches behind the decision
    alternatives: list[str]  # other agents whose rules also matched
    ambiguous: bool
    fallback: bool  # the research agent answered because no specialist applied
    file_path: str | None
    symbol: str | None
    section: str | None


class ActivityStep(BaseModel):
    agent: str
    task: str
    status: str  # running | completed | failed | skipped
    started_at: str
    completed_at: str | None
    duration_ms: int | None
    result: str | None


class AgentError(BaseModel):
    code: str
    message: str
    status_code: int
    retry_after_seconds: float | None = None


class AgentResponse(BaseModel):
    agent: str
    agent_title: str
    status: str  # completed | partial | failed | unsupported | needs_input
    answer: str  # Markdown; [S#] citations refer to sources
    sources: list[Source]
    metadata: dict[str, Any]  # agent-specific details (findings, impact, model, usage, ...)
    routing: Routing
    activity: list[ActivityStep]
    error: AgentError | None
    query: str
    repository_id: int
    took_ms: int
