"""Turn parsed code units into size-bounded chunks with exact line ranges.

Rules:
  - A structural unit (function, class, ...) that fits in CHUNK_SIZE is one chunk.
  - An oversized container with members (e.g. a class) is split into its members
    plus chunks for the code between them (signature, fields, ...).
  - Any other oversized unit is split into overlapping line windows ("parts").
  - Lines not covered by any unit become "module" chunks (top-level code).
  - Leading comments, decorators and annotations stay attached to their unit.

Invariant: chunk.content == the source lines start_line..end_line joined by "\\n"
(except for single lines longer than CHUNK_SIZE, which are cut into pieces).
"""

import hashlib
import re

from app import config
from app.models.chunk import CodeChunk
from app.services.parsers import FALLBACK, LINE_WINDOW
from app.services.parsers.base import CodeUnit, split_lines

# Bump when a change to the parsers/chunker alters the chunks produced for the same
# file. Stored indexes built with another version are rebuilt on the next analysis.
CHUNKER_VERSION = 1

_HAS_WORD = re.compile(r"\w")
_C_STYLE_LEADING = ("//", "/*", "*", "*/", "@")
LEADING_LINE_PREFIXES = {
    "python": ("#",),
    "javascript": _C_STYLE_LEADING, "jsx": _C_STYLE_LEADING,
    "typescript": _C_STYLE_LEADING, "tsx": _C_STYLE_LEADING,
    "java": _C_STYLE_LEADING, "go": _C_STYLE_LEADING,
    "c": (*_C_STYLE_LEADING, "template"), "cpp": (*_C_STYLE_LEADING, "template"),
    "csharp": (*_C_STYLE_LEADING, "["),
    "rust": (*_C_STYLE_LEADING, "#["),
}


def chunk_file(
    text: str,
    language: str,
    units: list[CodeUnit],
    *,
    repository: str,
    file_path: str,
    parser: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[CodeChunk]:
    if parser in (LINE_WINDOW, FALLBACK):
        gap_kind = "block"
    elif language == "markdown":
        gap_kind = "section"
    else:
        gap_kind = "module"

    chunker = _FileChunker(
        text, language, repository, file_path,
        chunk_size or config.CHUNK_SIZE,
        config.CHUNK_OVERLAP if chunk_overlap is None else chunk_overlap,
    )
    return chunker.run(units, gap_kind)


class _FileChunker:
    def __init__(self, text, language, repository, file_path, chunk_size, chunk_overlap):
        if not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap must be >= 0 and smaller than chunk_size")
        self.lines = split_lines(text)
        self.language = language
        self.repository = repository
        self.file_path = file_path
        self.size = chunk_size
        self.overlap = chunk_overlap
        self.leading = LEADING_LINE_PREFIXES.get(language, ())
        self.chunks: list[CodeChunk] = []

    def run(self, units: list[CodeUnit], gap_kind: str) -> list[CodeChunk]:
        if self.lines:
            self._cover(units, 1, len(self.lines), gap_kind, None, None)
        return self.chunks

    # -- structure

    def _cover(self, units, first, last, gap_kind, gap_name, gap_parent):
        """Emit chunks for `units` and for the uncovered lines between them."""
        cursor = first
        for unit in units:
            start = self._attach_leading_lines(unit.start_line, cursor)
            if start > cursor:
                self._gap(cursor, start - 1, gap_kind, gap_name, gap_parent)
            self._unit(unit, start)
            cursor = unit.end_line + 1
        if cursor <= last:
            self._gap(cursor, last, gap_kind, gap_name, gap_parent)

    def _unit(self, unit: CodeUnit, start: int):
        if unit.children and self._length(start, unit.end_line) > self.size:
            # Too big to keep whole: members become their own chunks; the code
            # around them (signature, fields, closing brace) is labelled with the container.
            self._cover(unit.children, start, unit.end_line, unit.kind, unit.name, unit.parent)
        else:
            self._emit(unit.kind, unit.name, unit.parent, start, unit.end_line)

    def _gap(self, first, last, kind, name, parent):
        while first <= last and not self.lines[first - 1].strip():
            first += 1
        while last >= first and not self.lines[last - 1].strip():
            last -= 1
        if first > last:
            return
        if not any(_HAS_WORD.search(self.lines[i]) for i in range(first - 1, last)):
            return  # only braces/punctuation, e.g. a lone "}" or "});"
        self._emit(kind, name, parent, first, last)

    def _attach_leading_lines(self, start: int, floor: int) -> int:
        """Move `start` up over directly preceding comment/decorator lines (no blank line between)."""
        if not self.leading:
            return start
        while start - 1 >= floor:
            previous = self.lines[start - 2].strip()
            if previous and previous.startswith(self.leading):
                start -= 1
            else:
                break
        return start

    # -- sizing

    def _length(self, first: int, last: int) -> int:
        return sum(len(self.lines[i]) for i in range(first - 1, last)) + (last - first)

    def _content(self, first: int, last: int) -> str:
        return "\n".join(self.lines[first - 1 : last])

    def _windows(self, first: int, last: int) -> list[tuple[int, int, str]]:
        """Split lines first..last into pieces of at most `size` chars, overlapping by ~`overlap` chars."""
        if self._length(first, last) <= self.size:
            return [(first, last, self._content(first, last))]

        pieces = []
        start = first
        while start <= last:
            line = self.lines[start - 1]
            if len(line) > self.size:  # a single huge line (e.g. minified code): cut it
                step = self.size - self.overlap
                for offset in range(0, len(line), step):
                    pieces.append((start, start, line[offset : offset + self.size]))
                    if offset + self.size >= len(line):
                        break
                start += 1
                continue

            end, total = start, len(line)
            while end + 1 <= last and len(self.lines[end]) <= self.size and total + 1 + len(self.lines[end]) <= self.size:
                total += 1 + len(self.lines[end])
                end += 1
            pieces.append((start, end, self._content(start, end)))
            if end >= last:
                break

            # Next window starts a few lines back so context carries over.
            next_start, carried = end + 1, 0
            while next_start - 1 > start and carried + len(self.lines[next_start - 2]) + 1 <= self.overlap:
                carried += len(self.lines[next_start - 2]) + 1
                next_start -= 1
            start = next_start
        return pieces

    def _emit(self, kind, name, parent, first, last):
        pieces = self._windows(first, last)
        total = len(pieces) if len(pieces) > 1 else None
        for index, (start, end, content) in enumerate(pieces, start=1):
            part = index if total else None
            self.chunks.append(
                CodeChunk(
                    chunk_id=_chunk_id(self.repository, self.file_path, start, end, part, content),
                    repository=self.repository,
                    file_path=self.file_path,
                    language=self.language,
                    chunk_type=kind,
                    symbol_name=name,
                    parent_symbol=parent,
                    start_line=start,
                    end_line=end,
                    part=part,
                    total_parts=total,
                    content=content,
                )
            )


def _chunk_id(repository, file_path, start, end, part, content) -> str:
    key = f"{repository}\n{file_path}\n{start}\n{end}\n{part}\n{content}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
