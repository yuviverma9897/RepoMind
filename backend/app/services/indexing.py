"""Analyze & index: GitHub metadata -> Phase 4 pipeline -> PostgreSQL.

Re-analysis is a file-level sync keyed on each file's sha256:
  unchanged files  -> stored chunks are kept as they are (ids, timestamps and,
                      later, embeddings survive)
  changed files    -> their chunks are replaced
  removed files    -> their chunks are deleted
  new files        -> chunks are inserted
If the chunker version or chunk settings differ from those of the stored index,
every file is treated as changed. The whole sync is one transaction: if anything
fails, the previous index stays intact.
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.db import database, store
from app.services import github
from app.services.chunker import CHUNKER_VERSION
from app.services.pipeline import analyze_repository


@dataclass
class SyncPlan:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def plan_sync(stored: dict[str, str], incoming: dict[str, str], rebuild: bool = False) -> SyncPlan:
    """Compare stored and incoming {file_path: file_hash} maps."""
    plan = SyncPlan()
    for path, digest in incoming.items():
        if path not in stored:
            plan.added.append(path)
        elif rebuild or stored[path] != digest:
            plan.changed.append(path)
        else:
            plan.unchanged.append(path)
    plan.removed = [path for path in stored if path not in incoming]
    return plan


def index_config(chunk_size: int, chunk_overlap: int) -> str:
    return f"v{CHUNKER_VERSION}:{chunk_size}:{chunk_overlap}"


async def index_repository(owner: str, repo: str, branch: str | None = None) -> dict:
    database.ensure_configured()  # fail before spending GitHub API requests
    started = time.monotonic()

    meta = await github.get_repository(owner, repo)  # 1 API request
    branch = branch or meta["default_branch"]
    # Always analyze the current snapshot (1 API request), never a cached result.
    analysis = await analyze_repository(meta["owner"], meta["name"], branch, refresh=True)

    result = await asyncio.to_thread(_persist, meta, branch, analysis)
    result["duration_ms"] = round((time.monotonic() - started) * 1000)

    # Automatically start embedding generation in background so search & copilot are immediately ready
    try:
        from app.services import embedding_service
        if embedding_service.provider_configured():
            asyncio.create_task(embedding_service.embed_repository(result["repository_id"]))
    except Exception:
        pass

    return result


def _persist(meta: dict, branch: str, analysis: dict) -> dict:
    chunks_by_file = defaultdict(list)
    for chunk in analysis["chunks"]:
        chunks_by_file[chunk.file_path].append(chunk)
    # Only files that produced chunks are stored (and therefore tracked).
    incoming = {f["path"]: f["file_hash"] for f in analysis["files"] if chunks_by_file[f["path"]]}
    config_key = index_config(analysis["chunk_size"], analysis["chunk_overlap"])

    with database.session_scope() as session:
        repository = store.upsert_repository(session, meta)
        plan = plan_sync(store.file_hashes(session, repository.id), incoming, rebuild=repository.index_config != config_key)

        deleted = store.delete_file_chunks(session, repository.id, plan.changed + plan.removed)
        rows = [
            {
                "repository_id": repository.id,
                "chunk_key": c.chunk_id,
                "file_path": c.file_path,
                "file_hash": incoming[path],
                "language": c.language,
                "chunk_type": c.chunk_type,
                "symbol_name": c.symbol_name,
                "parent_symbol": c.parent_symbol,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "part": c.part,
                "total_parts": c.total_parts,
                "content": c.content,
            }
            for path in plan.added + plan.changed
            for c in chunks_by_file[path]
        ]
        inserted = store.insert_chunks(session, rows)
        chunks_total = store.count_chunks(session, repository.id)

        languages = dict(sorted(analysis["languages"].items(), key=lambda kv: (-kv[1], kv[0])))
        stats = {
            "total_files": analysis["total_files"],
            "files_processed": analysis["processed_files"],
            "files_skipped": sum(analysis["skipped_files"].values()),
            "truncated": analysis["truncated"],
            "chunks_total": chunks_total,
            "language_files": languages,
            "chunk_types": analysis["chunk_types"],
        }
        changed_anything = bool(inserted or deleted) or repository.indexed_at is None
        repository.indexed_at = datetime.now(timezone.utc)
        repository.indexed_branch = branch
        repository.indexed_commit_sha = analysis["commit_sha"]
        repository.index_config = config_key
        repository.index_stats = stats

        return {
            "repository_id": repository.id,
            "repository": repository.full_name,
            "branch": branch,
            "commit_sha": analysis["commit_sha"],
            "status": "indexed" if changed_anything else "unchanged",
            **stats,
            "languages": list(languages),
            "chunks_created": inserted,
            "chunks_deleted": deleted,
            "files_added": len(plan.added),
            "files_updated": len(plan.changed),
            "files_removed": len(plan.removed),
            "files_unchanged": len(plan.unchanged),
            "indexed_at": repository.indexed_at,
        }


def index_status(owner: str, repo: str) -> dict:
    """Whether a repository is stored and indexed (database only, no GitHub request)."""
    with database.session_scope() as session:
        repository = store.find_repository(session, owner, repo)
        if repository is None or repository.indexed_at is None:
            return {"indexed": False, "repository_id": repository.id if repository else None}
        stats = repository.index_stats or {}
        return {
            "indexed": True,
            "repository_id": repository.id,
            "repository": repository.full_name,
            "indexed_at": repository.indexed_at,
            "indexed_branch": repository.indexed_branch,
            "commit_sha": repository.indexed_commit_sha,
            "files_processed": stats.get("files_processed"),
            "chunks_total": stats.get("chunks_total"),
            # JSONB does not keep key order, so sort by file count again.
            "languages": sorted(stats.get("language_files", {}), key=lambda k: (-stats["language_files"][k], k)),
        }


def stored_chunks(repository_id: int, page: int, page_size: int, file_path: str | None) -> dict:
    with database.session_scope() as session:
        repository = store.get_repository(session, repository_id)
        if repository is None:
            raise database.DatabaseError(404, "repository_not_indexed", f"No stored repository with id {repository_id}.")
        total = store.count_chunks(session, repository_id, file_path)
        chunks = store.list_chunks(session, repository_id, page=page, page_size=page_size, file_path=file_path)
        return {
            "repository_id": repository_id,
            "repository": repository.full_name,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "chunks": chunks,
        }
