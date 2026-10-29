import io

from app.services.parsers.base import split_lines
from app.services.pipeline import process_archive
from tests.helpers import SHA, make_archive

FILES = {
    "src/app.py": b"import os\n\ndef main():\n    return os.getcwd()\n",
    "src/util.js": b"export function add(a, b) {\n  return a + b;\n}\n",
    "README.md": b"# Project\n\nHello.\n",
    "data.json": b'{"a": 1}\n',
    "broken.py": b"def oops(:\n",
    "empty.py": b"",
    "node_modules/lib/index.js": b"module.exports = 1;\n",
    "yarn.lock": b"lock\n",
    "logo.png": b"\x89PNG\r\n\x1a\n\x00\x00",
    "notes.yml": b"a: 1\n",
    "fake.js": b"binary\x00content",
    "big.json": b"[" + b"1," * 150_000 + b"1]",
}


def test_pipeline_filters_parses_and_chunks():
    result = process_archive(make_archive(FILES, symlinks=["link.py"]), "owner/repo", "main")

    assert result["commit_sha"] == SHA
    assert result["total_files"] == len(FILES)  # symlinks are not counted
    assert result["processed_files"] == 5
    assert result["skipped_files"] == {
        "ignored_directory": 1, "generated_or_lock_file": 1, "binary": 2,
        "unsupported_language": 1, "too_large": 1, "empty": 1,
    }
    assert result["languages"] == {"python": 2, "javascript": 1, "markdown": 1, "json": 1}
    assert result["parsers"]["fallback"] == 1  # broken.py
    by_path = {f["path"]: f for f in result["files"]}
    assert "SyntaxError" in by_path["broken.py"]["note"]
    assert [f["path"] for f in result["files"]] == sorted(by_path)

    main = next(c for c in result["chunks"] if c.symbol_name == "main")
    assert (main.file_path, main.chunk_type, main.start_line, main.end_line) == ("src/app.py", "function", 3, 4)
    assert result["total_chunks"] == len(result["chunks"])

    # Line numbers match the original file contents.
    for c in result["chunks"]:
        lines = split_lines(FILES[c.file_path].decode())
        assert c.content == "\n".join(lines[c.start_line - 1 : c.end_line])


def test_file_limit_marks_result_truncated():
    files = {f"src/m{i}.py": b"x = 1\n" for i in range(5)}
    result = process_archive(make_archive(files), "owner/repo", "main", max_files=3)
    assert result["processed_files"] == 3
    assert result["truncated"] is True
    assert result["skipped_files"] == {"file_limit": 2}


def test_unreadable_archive_raises_clean_error():
    from app.services.github import GitHubError
    import pytest

    with pytest.raises(GitHubError) as info:
        process_archive(io.BytesIO(b"not a tarball"), "owner/repo", "main")
    assert info.value.code == "archive_invalid"


def test_file_limit_prefers_source_code_over_tests_and_docs():
    files = {
        "README.md": b"# Docs\n",
        "docs/guide.md": b"# Guide\n",
        "tests/test_core.py": b"def test_x():\n    pass\n",
        "src/core.py": b"def core():\n    return 1\n",
        "src/api.py": b"def api():\n    return 2\n",
    }
    result = process_archive(make_archive(files), "owner/repo", "main", max_files=3)
    assert [f["path"] for f in result["files"]] == ["src/api.py", "src/core.py", "tests/test_core.py"]
    assert result["skipped_files"] == {"file_limit": 2}
