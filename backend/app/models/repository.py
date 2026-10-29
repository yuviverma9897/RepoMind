from typing import Literal

from pydantic import BaseModel, Field


class ImportRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=500)


class RepositoryInfo(BaseModel):
    owner: str
    name: str
    full_name: str
    description: str | None
    html_url: str
    stars: int
    forks: int
    language: str | None
    default_branch: str


class FileEntry(BaseModel):
    path: str
    type: Literal["file", "directory"]


class TreeInfo(BaseModel):
    truncated: bool  # GitHub returned only part of a very large tree
    empty: bool  # repository has no commits yet
    file_count: int
    directory_count: int


class ImportResponse(BaseModel):
    repository: RepositoryInfo
    files: list[FileEntry]
    tree: TreeInfo


class FileContentResponse(BaseModel):
    path: str
    name: str
    language: str
    size: int  # bytes
    kind: Literal["text", "binary", "too_large"]
    content: str | None  # only set when kind == "text"
    message: str | None  # explanation when content is not shown
    html_url: str | None
    secrets_masked: int = 0  # detected secrets replaced by * (same length, so line numbers are unchanged)
