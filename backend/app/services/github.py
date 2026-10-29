"""GitHub REST API access: repository metadata/import, single files, archives.

- Import fetches only metadata and the file tree (exactly 2 API requests).
- File contents are fetched one file at a time, only when requested (1 request).
- Whole-repository analysis downloads one compressed archive (1 request) instead
  of making one request per file.
"""

import base64
import binascii
import re
import tempfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse

import httpx

from app import config
from app.services.file_types import detect_language, has_binary_extension, looks_binary

# GitHub usernames: alphanumerics and single hyphens, max 39 chars.
OWNER_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
# Repository names: letters, digits, '.', '-', '_', max 100 chars.
REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
# Characters git forbids in ref (branch) names, plus control characters.
INVALID_REF_PATTERN = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]|\.\.|@\{|//")


class GitHubError(Exception):
    """An expected failure that maps to a clean HTTP error response."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# ---------------------------------------------------------------- validation

def _is_valid_owner_repo(owner: str, repo: str) -> bool:
    return bool(OWNER_PATTERN.match(owner) and REPO_PATTERN.match(repo) and repo not in (".", ".."))


def parse_github_url(raw_url: str) -> tuple[str, str]:
    """Return (owner, repo) from a GitHub repository URL.

    Accepts e.g. https://github.com/owner/repo, github.com/owner/repo,
    .../repo.git, and deeper links like .../repo/tree/main/src.
    """
    url = raw_url.strip()
    if not url:
        raise GitHubError(400, "invalid_url", "Please enter a GitHub repository URL.")

    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "www.github.com"):
        raise GitHubError(400, "invalid_url", "Only github.com repository URLs are supported.")

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise GitHubError(
            400,
            "missing_owner_or_repo",
            "The URL must include both an owner and a repository, "
            "e.g. https://github.com/owner/repository.",
        )

    owner, repo = parts[0], parts[1]
    if repo.lower().endswith(".git"):
        repo = repo[:-4]

    if not _is_valid_owner_repo(owner, repo):
        raise GitHubError(
            400, "invalid_url", f"'{owner}/{repo}' is not a valid GitHub owner/repository name."
        )

    return owner, repo


def _validate_repo_ref(owner: str, repo: str, branch: str) -> None:
    if not _is_valid_owner_repo(owner, repo):
        raise GitHubError(400, "invalid_repository", f"'{owner}/{repo}' is not a valid owner/repository name.")

    if not branch or len(branch) > 255 or INVALID_REF_PATTERN.search(branch) or branch.startswith(("/", "-")):
        raise GitHubError(400, "invalid_branch", "The branch name is not valid.")


def _validate_file_params(owner: str, repo: str, branch: str, path: str) -> str:
    """Validate file-request parameters. Returns the normalized path."""
    _validate_repo_ref(owner, repo, branch)

    path = path.strip().strip("/")
    segments = path.split("/")
    if (
        not path
        or len(path) > 1000
        or any(seg in ("", ".", "..") for seg in segments)
        or re.search(r"[\x00-\x1f\x7f]", path)
    ):
        raise GitHubError(400, "invalid_path", "The file path is not valid.")
    return path


# ---------------------------------------------------------------- HTTP layer

def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "RepoMind",
    }
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return headers


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=config.GITHUB_API_URL,
        headers=_headers(),
        timeout=config.GITHUB_TIMEOUT_SECONDS,
        # GitHub answers 301 for renamed/transferred repositories.
        follow_redirects=True,
    )


def _raise_for_github_error(
    response: httpx.Response, owner: str, repo: str, not_found_message: str, not_found_code: str = "repo_not_found"
) -> None:
    """Translate a non-success GitHub response into a GitHubError."""
    status = response.status_code
    if status < 400:
        return

    try:
        body = response.json()
        gh_message = body.get("message", "") if isinstance(body, dict) else ""
    except ValueError:
        gh_message = ""

    remaining = response.headers.get("x-ratelimit-remaining")
    is_rate_limited = status == 429 or (
        status == 403 and (remaining == "0" or "rate limit" in gh_message.lower())
    )
    if is_rate_limited:
        reset = response.headers.get("x-ratelimit-reset")
        when = ""
        if reset and reset.isdigit():
            reset_at = datetime.fromtimestamp(int(reset), tz=timezone.utc)
            minutes = max(1, round((reset_at - datetime.now(timezone.utc)).total_seconds() / 60))
            when = f" Try again in about {minutes} minute(s)."
        hint = "" if config.GITHUB_TOKEN else " Setting a GITHUB_TOKEN raises the limit."
        raise GitHubError(429, "rate_limited", f"GitHub API rate limit exceeded.{when}{hint}")

    if status == 401:
        raise GitHubError(
            502, "github_auth_failed", "GitHub rejected the configured GITHUB_TOKEN. Check that it is valid."
        )

    if status == 404:
        raise GitHubError(404, not_found_code, not_found_message)

    if status == 403:
        raise GitHubError(403, "access_denied", f"GitHub denied access to '{owner}/{repo}'. {gh_message}".strip())

    if status == 451:
        raise GitHubError(451, "repo_unavailable", f"Repository '{owner}/{repo}' is unavailable for legal reasons.")

    raise GitHubError(502, "github_error", f"GitHub API returned an unexpected error ({status}). Please try again.")


def _network_error(exc: httpx.RequestError) -> GitHubError:
    if isinstance(exc, httpx.TimeoutException):
        return GitHubError(504, "github_timeout", "GitHub API did not respond in time. Please try again.")
    return GitHubError(503, "github_unreachable", "Could not connect to GitHub. Check the server's network connection.")


async def _get(client: httpx.AsyncClient, path: str, headers: dict | None = None, **params) -> httpx.Response:
    try:
        return await client.get(path, params=params or None, headers=headers)
    except httpx.RequestError as exc:
        raise _network_error(exc)


def _json_or_error(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        raise GitHubError(502, "github_error", "GitHub returned an invalid response.")


def _repo_not_found(owner: str, repo: str) -> str:
    # GitHub returns 404 for private repos too, to avoid leaking their existence.
    return (
        f"Repository '{owner}/{repo}' was not found. It may not exist or may be private "
        "(only public repositories are supported)."
    )


# ---------------------------------------------------------------- import

async def _fetch_repository(client: httpx.AsyncClient, owner: str, repo: str) -> dict:
    """Fetch and normalise repository metadata (1 API request). Rejects private repos."""
    meta_response = await _get(client, f"/repos/{owner}/{repo}")
    _raise_for_github_error(meta_response, owner, repo, _repo_not_found(owner, repo))
    meta = _json_or_error(meta_response)
    if not isinstance(meta, dict) or not isinstance(meta.get("owner"), dict) or "name" not in meta:
        raise GitHubError(502, "github_error", "GitHub returned unexpected repository data.")

    if meta.get("private"):
        raise GitHubError(403, "private_repo", "This repository is private. Only public repositories are supported.")

    return {
        "github_id": meta.get("id"),  # stable across renames; used as the database identity
        "owner": meta["owner"]["login"],
        "name": meta["name"],
        "full_name": meta["full_name"],
        "description": meta.get("description"),
        "html_url": meta["html_url"],
        "stars": meta.get("stargazers_count", 0),
        "forks": meta.get("forks_count", 0),
        "language": meta.get("language"),
        "default_branch": meta.get("default_branch") or "main",
    }


async def get_repository(owner: str, repo: str) -> dict:
    """Repository metadata only (1 API request)."""
    if not _is_valid_owner_repo(owner, repo):
        raise GitHubError(400, "invalid_repository", f"'{owner}/{repo}' is not a valid owner/repository name.")
    async with _client() as client:
        return await _fetch_repository(client, owner, repo)


async def import_repository(raw_url: str) -> dict:
    owner, repo = parse_github_url(raw_url)

    async with _client() as client:
        # 1) Repository metadata
        repository = await _fetch_repository(client, owner, repo)

        # 2) Whole file tree in a single request (Git Trees API, recursive).
        # Use the canonical names GitHub returned (handles renamed repos/case).
        branch = quote(repository["default_branch"], safe="")
        tree_response = await _get(
            client,
            f"/repos/{repository['owner']}/{repository['name']}/git/trees/{branch}",
            recursive="1",
        )

        # 409 = repository has no commits yet; 404 here = branch has no tree.
        if tree_response.status_code in (404, 409):
            return {
                "repository": repository,
                "files": [],
                "tree": {"truncated": False, "empty": True, "file_count": 0, "directory_count": 0},
            }
        _raise_for_github_error(tree_response, owner, repo, _repo_not_found(owner, repo))
        tree_data = _json_or_error(tree_response)
        if not isinstance(tree_data, dict):
            raise GitHubError(502, "github_error", "GitHub returned unexpected tree data.")

    files = []
    for item in tree_data.get("tree", []):
        # "blob" = file, "tree" = directory. "commit" entries are git submodules;
        # they are skipped for now since their contents live in another repo.
        if item.get("type") == "blob":
            files.append({"path": item["path"], "type": "file"})
        elif item.get("type") == "tree":
            files.append({"path": item["path"], "type": "directory"})

    file_count = sum(1 for f in files if f["type"] == "file")
    return {
        "repository": repository,
        "files": files,
        "tree": {
            "truncated": bool(tree_data.get("truncated")),
            "empty": len(files) == 0,
            "file_count": file_count,
            "directory_count": len(files) - file_count,
        },
    }


# ---------------------------------------------------------------- file contents

async def get_file_content(owner: str, repo: str, branch: str, path: str) -> dict:
    """Fetch one file via the Contents API and return it as displayable text.

    Binary and oversized files are reported (kind="binary"/"too_large")
    with content=None instead of being sent to the browser.
    """
    path = _validate_file_params(owner, repo, branch, path)

    async with _client() as client:
        # The "object" media type works for files up to 100 MB (content is omitted
        # above 1 MB) and returns directories as {"type": "dir", ...}.
        response = await _get(
            client,
            f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}",
            headers={"Accept": "application/vnd.github.object+json"},
            ref=branch,
        )
        _raise_for_github_error(
            response,
            owner,
            repo,
            f"File '{path}' was not found on branch '{branch}' of '{owner}/{repo}'. "
            "The repository, branch, or path may not exist.",
            not_found_code="file_not_found",
        )
        data = _json_or_error(response)

    if not isinstance(data, dict):
        raise GitHubError(502, "github_error", "GitHub returned unexpected file data.")
    if data.get("type") == "dir":
        raise GitHubError(400, "not_a_file", f"'{path}' is a directory, not a file.")
    if data.get("type") != "file":
        kind = data.get("type", "unknown")
        raise GitHubError(400, "not_a_file", f"'{path}' is a {kind}, not a regular file, and can't be displayed.")

    size = int(data.get("size") or 0)
    result = {
        "path": data.get("path", path),
        "name": data.get("name") or PurePosixPath(path).name,
        "language": detect_language(path),
        "size": size,
        "kind": "text",
        "content": None,
        "message": None,
        "html_url": data.get("html_url"),
    }

    binary = {**result, "kind": "binary", "language": "binary",
              "message": "This is a binary file and can't be displayed as text."}
    if has_binary_extension(path):
        return binary

    if size > config.MAX_FILE_SIZE_BYTES:
        limit_kb = config.MAX_FILE_SIZE_BYTES // 1024
        return {
            **result,
            "kind": "too_large",
            "message": f"This file is {size / 1024:,.0f} KB, above the {limit_kb:,} KB viewer limit.",
        }

    # The Contents API returns base64 for files up to 1 MB. Anything else
    # (e.g. encoding "none") would already have been caught by the size limit.
    if data.get("encoding") != "base64":
        raise GitHubError(502, "unsupported_encoding", "GitHub returned this file in an unsupported encoding.")

    # GitHub wraps base64 at 60 chars; remove whitespace, then decode strictly.
    encoded = "".join((data.get("content") or "").split())
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise GitHubError(502, "decode_failed", "Could not decode the file returned by GitHub.")
    if len(raw) != size:
        raise GitHubError(502, "decode_failed", "The file returned by GitHub was incomplete. Please try again.")

    if looks_binary(raw):
        return binary

    try:
        text = raw.decode("utf-8-sig")  # also strips a UTF-8 byte-order mark
    except UnicodeDecodeError:
        return {**binary, "message": "This file is not valid UTF-8 text, so it can't be displayed."}

    return {**result, "content": text}


# ---------------------------------------------------------------- archive download

async def download_archive(owner: str, repo: str, branch: str):
    """Download the repository snapshot for `branch` as a .tar.gz (one API request).

    Returns a seekable temporary file (kept in memory while small, spilled to
    disk when large). The caller is responsible for closing it.
    """
    _validate_repo_ref(owner, repo, branch)
    limit = config.MAX_ARCHIVE_BYTES
    limit_text = f"{limit // (1024 * 1024)} MB" if limit >= 1024 * 1024 else f"{limit // 1024} KB"
    buffer = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)

    try:
        async with _client() as client:
            # Redirects to codeload.github.com, which serves the archive.
            async with client.stream("GET", f"/repos/{owner}/{repo}/tarball/{quote(branch, safe='')}") as response:
                if response.status_code >= 400:
                    await response.aread()
                    _raise_for_github_error(
                        response,
                        owner,
                        repo,
                        f"Repository '{owner}/{repo}' or branch '{branch}' was not found. "
                        "It may not exist or may be private.",
                    )

                declared = int(response.headers.get("content-length") or 0)
                if declared > config.MAX_ARCHIVE_BYTES:
                    raise GitHubError(413, "archive_too_large", f"Repository archive exceeds the {limit_text} analysis limit.")

                received = 0
                async for data in response.aiter_bytes():
                    received += len(data)
                    if received > config.MAX_ARCHIVE_BYTES:
                        raise GitHubError(
                            413, "archive_too_large", f"Repository archive exceeds the {limit_text} analysis limit."
                        )
                    buffer.write(data)
    except httpx.RequestError as exc:
        buffer.close()
        raise _network_error(exc)
    except BaseException:
        buffer.close()
        raise

    buffer.seek(0)
    return buffer
