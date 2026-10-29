"""Read a repository's stored code back from code_chunks.

Chunks cover every line of an indexed file and their content is exactly their source lines
(see chunker.py), so files can be rebuilt locally without calling GitHub again. Shared by code
intelligence, dependency analysis and the debug/security scans.
"""

from app.db import database, store
from app.db.database import DatabaseError
from app.services.parsers import parse_source
from app.services.search import SearchError

CONTAINER_KINDS = {"class", "interface", "struct", "enum", "trait", "impl", "type"}


class AnalysisError(SearchError):
    """A client error from an analysis feature, reported as {detail, code} by the SearchError handler."""


def require_repository(session, repository_id: int):
    repository = store.get_repository(session, repository_id)
    if repository is None:
        raise DatabaseError(404, "repository_not_indexed", f"No stored repository with id {repository_id}.")
    return repository


def load_file(repository_id: int, file_path: str) -> dict:
    """{"file_path", "language", "lines", "line_count", "symbols", "chunks"} rebuilt from stored chunks."""
    with database.session_scope() as session:
        repository = require_repository(session, repository_id)
        rows = store.file_chunks(session, repository_id, file_path)
        full_name = repository.full_name
    if not rows:
        raise AnalysisError(404, "file_not_indexed",
                            f"{file_path} has no stored chunks. It may have been skipped during analysis "
                            "(binary, too large or vendored) or the repository needs to be analyzed again.")
    lines: dict[int, str] = {}
    long_line_pieces: dict[int, list[str]] = {}
    for row in rows:
        if row.start_line == row.end_line and row.total_parts:  # piece of one huge line
            long_line_pieces.setdefault(row.start_line, []).append(row.content)
            continue
        for offset, text in enumerate(row.content.split("\n")):
            lines.setdefault(row.start_line + offset, text)
    for number, pieces in long_line_pieces.items():
        lines.setdefault(number, "".join(pieces))
    line_count = max(lines)
    text_lines = [lines.get(i, "") for i in range(1, line_count + 1)]
    return {
        "repository": full_name,
        "file_path": file_path,
        "language": rows[0].language,
        "lines": text_lines,
        "line_count": line_count,
        "symbols": parsed_symbols("\n".join(text_lines), rows[0].language) or symbols_from_rows(rows),
        "chunks": rows,
    }


def parsed_symbols(text: str, language: str) -> list[dict]:
    """All functions/classes/methods, including members of small classes stored as a single chunk."""
    if language == "markdown":
        return []
    symbols = []
    for unit in parse_source(text, language).units:  # same parsers as indexing (exact for Python)
        if unit.kind == "imports" or not unit.name:
            continue
        symbols.append({"kind": unit.kind, "name": unit.name, "parent": unit.parent,
                        "start_line": unit.start_line, "end_line": unit.end_line})
        for child in unit.children:
            if child.name:
                symbols.append({"kind": child.kind, "name": child.name, "parent": child.parent or unit.name,
                                "start_line": child.start_line, "end_line": child.end_line})
    return symbols


def symbols_from_rows(rows) -> list[dict]:
    """Merge chunk parts back into symbols; containers span their members (split classes)."""
    symbols: list[dict] = []
    for row in rows:
        if not row.symbol_name:
            continue
        prev = symbols[-1] if symbols else None
        same = prev and (prev["kind"], prev["parent"], prev["name"]) == (row.chunk_type, row.parent_symbol, row.symbol_name)
        if same and row.start_line <= prev["end_line"] + 1:
            prev["end_line"] = max(prev["end_line"], row.end_line)
            continue
        symbols.append({"kind": row.chunk_type, "name": row.symbol_name, "parent": row.parent_symbol,
                        "start_line": row.start_line, "end_line": row.end_line})
    for symbol in symbols:
        if symbol["kind"] in CONTAINER_KINDS:
            members = [s["end_line"] for s in symbols if s["parent"] == symbol["name"] and s["start_line"] >= symbol["start_line"]]
            symbol["end_line"] = max([symbol["end_line"], *members])
    return symbols


def slice_lines(file: dict, start: int, end: int) -> str:
    return "\n".join(file["lines"][start - 1:end])
