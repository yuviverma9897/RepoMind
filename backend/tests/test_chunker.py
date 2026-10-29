from app.services.parsers.base import split_lines
from tests.helpers import chunk, symbols

PYTHON = '''import os
from typing import Any

# Greets someone.
@cache
def greet(name: str) -> str:
    return f"hi {name}"


class User:
    """A user."""

    def __init__(self, name):
        self.name = name

    async def save(self):
        return True


if __name__ == "__main__":
    greet("x")
'''


def test_python_structures_and_exact_lines():
    result, chunks = chunk(PYTHON, "python")
    assert result.parser == "python-ast"
    assert symbols(chunks) == [
        ("imports", None, 1, 2),
        ("function", "greet", 4, 7),  # leading comment + decorator included
        ("class", "User", 10, 17),
        ("module", None, 20, 21),
    ]


def test_large_class_is_split_into_methods_with_parent():
    methods = "\n".join(f"    def method_{i}(self):\n        return {i} * 2 + 1  # padding text\n" for i in range(40))
    source = f"class Big:\n    x = 1\n\n{methods}"
    _, chunks = chunk(source, "python", size=500, overlap=50)
    header = chunks[0]
    assert (header.chunk_type, header.symbol_name, header.start_line) == ("class", "Big", 1)
    method_chunks = [c for c in chunks if c.chunk_type == "method"]
    assert len(method_chunks) == 40
    assert all(c.parent_symbol == "Big" for c in method_chunks)
    assert method_chunks[0].symbol_name == "method_0"


def test_oversized_function_split_into_overlapping_parts():
    body = "\n".join(f"    value_{i} = compute_something({i})" for i in range(120))
    source = f"def huge():\n{body}\n"
    _, chunks = chunk(source, "python", size=600, overlap=100)
    assert len(chunks) > 1
    assert all(c.symbol_name == "huge" and c.chunk_type == "function" for c in chunks)
    assert [c.part for c in chunks] == list(range(1, len(chunks) + 1))
    assert all(c.total_parts == len(chunks) for c in chunks)
    # Parts cover the whole function, in order, with overlap between neighbours.
    assert chunks[0].start_line == 1 and chunks[-1].end_line == 121
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev.start_line < nxt.start_line <= prev.end_line


def test_zero_overlap_produces_adjacent_parts():
    source = "\n".join(f"line number {i} with some text" for i in range(200))
    _, chunks = chunk(source, "json", size=400, overlap=0)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start_line == prev.end_line + 1


def test_fallback_windows_cover_every_line():
    source = "\n".join(f'  "key_{i}": "value number {i}",' for i in range(300))
    result, chunks = chunk(source, "json", size=500, overlap=50)
    assert result.parser == "line-window"
    assert {c.chunk_type for c in chunks} == {"block"}
    covered = set()
    for c in chunks:
        covered.update(range(c.start_line, c.end_line + 1))
    assert covered == set(range(1, 301))


def test_python_syntax_error_falls_back_instead_of_failing():
    source = "def broken(:\n    pass\n" + "x = 1\n" * 50
    result, chunks = chunk(source, "python")
    assert result.parser == "fallback"
    assert "SyntaxError" in result.error
    assert chunks and chunks[0].chunk_type == "block"


def test_empty_and_whitespace_files_produce_no_chunks():
    assert chunk("", "python")[1] == []
    assert chunk("\n\n   \n", "javascript")[1] == []


def test_huge_single_line_is_cut_into_bounded_pieces():
    source = "var x=" + "a+" * 5000 + "1;"
    _, chunks = chunk(source, "javascript", size=1000, overlap=100)
    assert len(chunks) > 5
    assert all(c.start_line == c.end_line == 1 for c in chunks)


def test_punctuation_only_gaps_are_dropped():
    source = "function a() {\n  return 1;\n}\n\n});\n"
    _, chunks = chunk(source, "javascript")
    assert symbols(chunks) == [("function", "a", 1, 3)]


def test_chunk_ids_are_deterministic_and_unique():
    _, first = chunk(PYTHON, "python")
    _, second = chunk(PYTHON, "python")
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert len({c.chunk_id for c in first}) == len(first)


def test_split_lines_matches_editor_numbering():
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a\nb") == ["a", "b"]
    assert split_lines("") == []
