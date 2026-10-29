"""Request/response shapes for dependency graphs, architecture summaries and impact analysis."""

from typing import Any

from pydantic import BaseModel, Field

from app.models.rag import Citations, Evidence, Source, TokenUsage


class GraphNode(BaseModel):
    id: str
    label: str
    type: str  # directory | file | package (Go) | external
    language: str | None
    files: int
    in_degree: int
    out_degree: int
    file: str | None = None  # repository path for file nodes


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str = "imports"  # relationship kind (only static imports are detected)
    weight: int  # number of import statements


class GraphResponse(BaseModel):
    repository_id: int
    level: str
    depth: int
    path_prefix: str | None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    total_nodes: int  # before the max_nodes limit
    total_edges: int
    truncated: bool
    stats: dict[str, Any]
    limitations: list[str]


class SummaryRequest(BaseModel):
    path_prefix: str | None = Field(None, max_length=1000)


class GroundedFields(BaseModel):
    answer: str
    model: str
    finish_reason: str
    truncated: bool
    usage: TokenUsage
    sources: list[Source]
    citations: Citations
    evidence: Evidence
    took_ms: int


class SummaryResponse(GroundedFields):
    repository_id: int
    repository: str
    path_prefix: str | None
    facts: dict[str, Any]


class ImpactRequest(BaseModel):
    file_path: str = Field(min_length=1, max_length=1000)
    symbol: str | None = Field(None, max_length=200)
    depth: int = 2  # 1-3 levels of importers


class Dependent(BaseModel):
    file_path: str
    lines: list[int]
    distance: int
    via: str | None = None


class Reference(BaseModel):
    file_path: str
    line: int
    text: str
    same_file: bool
    file_imports_target: bool


class ImportInfo(BaseModel):
    specifier: str
    status: str
    target: str | None
    line: int


class ImpactResponse(BaseModel):
    repository_id: int
    file_path: str
    language: str
    symbol: str | None
    depth: int
    imports_supported: bool
    direct_dependents: list[Dependent]
    transitive_dependents: list[Dependent]
    dependencies: list[ImportInfo]
    references: list[Reference]
    truncated: bool
    limitations: list[str]


class ImpactExplanation(GroundedFields):
    impact: ImpactResponse
