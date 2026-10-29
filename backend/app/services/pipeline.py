"""Repository analysis pipeline: archive -> filter -> parse -> chunk -> summary.

The repository is downloaded once as a .tar.gz (one GitHub API request) and
read entirely in memory: nothing is extracted to disk. Results are cached
briefly so paging through chunks does not re-download the repository.
"""

import asyncio
import hashlib
import logging
import tarfile
import time
from collections import Counter

from app import config
from app.services import file_filter
from app.services.chunker import chunk_file
from app.services.file_types import detect_language
from app.services.github import GitHubError, download_archive
from app.services.parsers import FALLBACK, parse_source
from app.services.parsers.base import count_lines

log = logging.getLogger(__name__)

FILE_LIMIT = "file_limit"  # skip reason when MAX_FILES_PER_ANALYSIS is reached

_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}
_locks: dict[tuple[str, str, str], asyncio.Lock] = {}


async def analyze_repository(owner: str, repo: str, branch: str, *, refresh: bool = False) -> dict:
    """Analyze a repository snapshot. refresh=True ignores (and replaces) the cached result."""
    key = (owner.lower(), repo.lower(), branch)
    lock = _locks.setdefault(key, asyncio.Lock())  # concurrent identical requests share one download
    try:
        async with lock:
            cached = _cache.get(key)
            if cached and not refresh and time.monotonic() - cached[0] < config.ANALYSIS_CACHE_TTL_SECONDS:
                return cached[1]

            archive = await download_archive(owner, repo, branch)
            try:
                result = await asyncio.to_thread(process_archive, archive, f"{owner}/{repo}", branch)
            finally:
                archive.close()

            _cache[key] = (time.monotonic(), result)
            while len(_cache) > max(config.ANALYSIS_CACHE_SIZE, 0):
                oldest = min(_cache, key=lambda k: _cache[k][0])
                del _cache[oldest]
            return result
    finally:
        if not lock.locked():
            _locks.pop(key, None)


def process_archive(
    fileobj,
    repository: str,
    branch: str,
    *,
    max_files: int | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> dict:
    """Filter, parse and chunk every file in a GitHub .tar.gz archive."""
    max_files = config.MAX_FILES_PER_ANALYSIS if max_files is None else max_files
    total_files = 0
    skipped: Counter = Counter()
    languages: Counter = Counter()
    parsers: Counter = Counter()
    per_file: dict[str, tuple[dict, list]] = {}
    commit_sha = None

    try:
        with tarfile.open(fileobj=fileobj, mode="r:gz") as tar:
            # Pass 1: headers only. Decide which files to process.
            candidates = []
            for member in tar.getmembers():
                if not member.isfile():
                    continue  # directories, symlinks, ...
                # GitHub prefixes every path with "<owner>-<repo>-<sha>/".
                _, _, path = member.name.partition("/")
                if not path:
                    continue
                total_files += 1
                reason = file_filter.check_path(path, member.size)
                if reason:
                    skipped[reason] += 1
                else:
                    candidates.append((member, path))

            if len(candidates) > max_files:
                # Over the limit: keep the most useful files (source code first),
                # preserving archive order so the archive is still read front to back.
                ranked = sorted(range(len(candidates)), key=lambda i: (_priority(candidates[i][1]), i))
                keep = set(ranked[:max_files])
                skipped[FILE_LIMIT] = len(candidates) - max_files
                candidates = [c for i, c in enumerate(candidates) if i in keep]

            # Pass 2: read, parse and chunk the selected files.
            for member, path in candidates:
                extracted = tar.extractfile(member)
                raw = extracted.read() if extracted else b""
                text, reason = file_filter.decode_source(raw)
                if reason:
                    skipped[reason] += 1
                    continue

                language = detect_language(path)
                chunks, parser, note = _chunk_one(text, language, repository, path, chunk_size, chunk_overlap)
                languages[language] += 1
                parsers[parser] += 1
                summary = {
                    "path": path,
                    "language": language,
                    "parser": parser,
                    "line_count": count_lines(text),
                    "chunk_count": len(chunks),
                    "note": note,
                    "file_hash": hashlib.sha256(raw).hexdigest(),  # detects unchanged files on re-index
                }
                per_file[path] = (summary, chunks)

            # git archive stores the commit id in the global pax header.
            commit_sha = tar.pax_headers.get("comment")
    except (tarfile.TarError, EOFError, OSError) as exc:
        log.warning("Could not read archive for %s: %r", repository, exc)
        raise GitHubError(502, "archive_invalid", "GitHub returned an unreadable repository archive.")

    files, chunks = [], []
    for path in sorted(per_file):
        summary, file_chunks = per_file[path]
        files.append(summary)
        chunks.extend(file_chunks)

    return {
        "repository": repository,
        "branch": branch,
        "commit_sha": commit_sha,
        "total_files": total_files,
        "processed_files": len(files),
        "skipped_files": dict(skipped.most_common()),
        "truncated": skipped[FILE_LIMIT] > 0,
        "languages": dict(languages.most_common()),
        "chunk_types": dict(Counter(c.chunk_type for c in chunks).most_common()),
        "parsers": dict(parsers.most_common()),
        "total_chunks": len(chunks),
        "chunk_size": chunk_size or config.CHUNK_SIZE,
        "chunk_overlap": config.CHUNK_OVERLAP if chunk_overlap is None else chunk_overlap,
        "files": files,
        "chunks": chunks,
    }


_DOC_LANGUAGES = frozenset({"markdown", "json", "html", "css"})
_TEST_DIRECTORIES = frozenset({"test", "tests", "__tests__", "spec", "specs", "fixtures", "__fixtures__",
                               "testdata", "examples", "example", "e2e", "benchmarks"})


def _priority(path: str) -> int:
    """Lower is more important when MAX_FILES_PER_ANALYSIS forces a choice."""
    if detect_language(path) in _DOC_LANGUAGES:
        return 2
    if any(part.lower() in _TEST_DIRECTORIES for part in path.split("/")[:-1]):
        return 1
    return 0


def _chunk_one(text, language, repository, path, chunk_size, chunk_overlap):
    """Parse and chunk one file. Never raises: falls back to plain line windows."""
    result = parse_source(text, language)
    options = dict(repository=repository, file_path=path, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    try:
        chunks = chunk_file(text, language, result.units, parser=result.parser, **options)
        return chunks, result.parser, result.error
    except Exception as exc:
        log.warning("Chunking failed for %s, using line windows: %r", path, exc)
        chunks = chunk_file(text, language, [], parser=FALLBACK, **options)
        return chunks, FALLBACK, f"chunker failed: {type(exc).__name__}"
