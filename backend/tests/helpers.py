import io
import tarfile

from app.services.chunker import chunk_file
from app.services.parsers import parse_source
from app.services.parsers.base import split_lines


def chunk(text, language, path="file", size=1500, overlap=150):
    result = parse_source(text, language)
    chunks = chunk_file(
        text, language, result.units,
        repository="owner/repo", file_path=path, parser=result.parser,
        chunk_size=size, chunk_overlap=overlap,
    )
    assert_line_numbers_exact(text, chunks, size)
    return result, chunks


def assert_line_numbers_exact(text, chunks, size):
    """Every chunk's content must be exactly its source lines (the core invariant)."""
    lines = split_lines(text)
    for c in chunks:
        assert 1 <= c.start_line <= c.end_line <= len(lines), c
        expected = "\n".join(lines[c.start_line - 1 : c.end_line])
        if c.start_line == c.end_line and len(expected) > size:
            assert c.content in expected  # piece of a huge single line
        else:
            assert c.content == expected, (c.start_line, c.end_line)
        assert len(c.content) <= size, "chunk exceeds CHUNK_SIZE"


def symbols(chunks):
    return [(c.chunk_type, c.symbol_name, c.start_line, c.end_line) for c in chunks]


SHA = "0123456789abcdef0123456789abcdef01234567"


def make_archive(files: dict[str, bytes], symlinks=()) -> io.BytesIO:
    """Build an archive shaped like GitHub's: prefix dir + commit SHA in the pax header."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": SHA}) as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(f"owner-repo-0123456/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for path in symlinks:
            info = tarfile.TarInfo(f"owner-repo-0123456/{path}")
            info.type = tarfile.SYMTYPE
            info.linkname = "elsewhere"
            tar.addfile(info)
    buffer.seek(0)
    return buffer


def index_files(github_id: int, name: str, files: dict[str, str], chunk_size: int = 1500, overlap: int = 150) -> int:
    """Store real source files as chunks (real parser + chunker), like an analyzed repository."""
    from app.db import database, store
    from app.services.file_types import detect_language
    from tests.test_db import META

    with database.session_scope() as s:
        repo = store.upsert_repository(s, {**META, "github_id": github_id, "name": name, "full_name": f"o/{name}"})
        rows = []
        for path, text in files.items():
            language = detect_language(path)
            result = parse_source(text, language)
            for c in chunk_file(text, language, result.units, repository=f"o/{name}", file_path=path,
                                parser=result.parser, chunk_size=chunk_size, chunk_overlap=overlap):
                rows.append({"repository_id": repo.id, "chunk_key": c.chunk_id, "file_path": path, "file_hash": "b" * 64,
                             "language": c.language, "chunk_type": c.chunk_type, "symbol_name": c.symbol_name,
                             "parent_symbol": c.parent_symbol, "start_line": c.start_line, "end_line": c.end_line,
                             "part": c.part, "total_parts": c.total_parts, "content": c.content})
        store.insert_chunks(s, rows)
        return repo.id
