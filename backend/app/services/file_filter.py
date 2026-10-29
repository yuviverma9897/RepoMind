"""Decide which repository files are worth parsing and chunking.

Every rejected file gets a short reason so the analysis summary can explain
what was skipped. To support a new language, add it to SUPPORTED_LANGUAGES
(and optionally give it a parser in services/parsers).
"""

from pathlib import PurePosixPath

from app import config
from app.services.file_types import detect_language, has_binary_extension, looks_binary

SUPPORTED_LANGUAGES = frozenset({
    "python", "javascript", "jsx", "typescript", "tsx",
    "java", "c", "cpp", "csharp", "go", "rust",
    "html", "css", "json", "markdown",
})

# Directory names whose contents are dependencies, build output or tool caches.
IGNORED_DIRECTORIES = frozenset({
    ".git", "node_modules", "bower_components", "vendor", "third_party",
    "dist", "build", "out", "target", "coverage",
    "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    ".next", ".nuxt", ".cache", ".gradle", ".idea", ".vscode",
})

# Generated files that are technically text but useless to search.
LOCK_FILES = frozenset({"package-lock.json", "pnpm-lock.yaml", "go.sum"})
GENERATED_SUFFIXES = (".lock", ".min.js", ".min.css", ".map")

# Skip reasons (also used as keys in the API summary)
IGNORED_DIRECTORY = "ignored_directory"
GENERATED = "generated_or_lock_file"
BINARY = "binary"
UNSUPPORTED = "unsupported_language"
TOO_LARGE = "too_large"
EMPTY = "empty"


def check_path(path: str, size: int) -> str | None:
    """Return a skip reason based on the path and size alone, or None to keep the file."""
    parts = PurePosixPath(path).parts
    if any(part in IGNORED_DIRECTORIES for part in parts[:-1]):
        return IGNORED_DIRECTORY

    name = parts[-1].lower()
    if name in LOCK_FILES or name.endswith(GENERATED_SUFFIXES):
        return GENERATED
    if has_binary_extension(name):
        return BINARY
    if detect_language(path) not in SUPPORTED_LANGUAGES:
        return UNSUPPORTED
    if size > config.MAX_CHUNK_FILE_BYTES:
        return TOO_LARGE
    return None


def decode_source(raw: bytes) -> tuple[str | None, str | None]:
    """Return (text, None) for usable source, or (None, skip_reason).

    Line endings are normalized to "\\n" so every parser and the chunker agree
    on line numbers.
    """
    if looks_binary(raw):
        return None, BINARY
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, BINARY
    if not text.strip():
        return None, EMPTY
    return text.replace("\r\n", "\n").replace("\r", "\n"), None
