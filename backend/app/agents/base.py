"""Shared result shape and the activity recorder (every step is a real, timed call)."""

import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from app.db.database import DatabaseError
from app.services.embeddings import EmbeddingError
from app.services.github import GitHubError
from app.services.search import SearchError

# Service errors that should become an agent failure (with the service's own message) instead of an HTTP error.
SERVICE_ERRORS = (SearchError, EmbeddingError, DatabaseError, GitHubError)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Activity:
    """Records what actually ran: one entry per step, with real timings and outcome."""

    def __init__(self):
        self.steps: list[dict] = []

    @asynccontextmanager
    async def step(self, agent: str, task: str):
        entry = {"agent": agent, "task": task, "status": "running", "started_at": _now(), "completed_at": None,
                 "duration_ms": None, "result": None}
        self.steps.append(entry)
        started = time.monotonic()
        try:
            yield entry  # the caller sets entry["result"] to a short outcome summary
            entry["status"] = "completed"
        except Exception as error:
            entry["status"] = "failed"
            entry["result"] = getattr(error, "message", None) or f"Internal error ({type(error).__name__})"
            raise
        finally:
            entry["completed_at"] = _now()
            entry["duration_ms"] = round((time.monotonic() - started) * 1000)

    def skipped(self, agent: str, task: str, reason: str):
        """A step that was considered but not run (e.g. AI review without a configured model)."""
        self.steps.append({"agent": agent, "task": task, "status": "skipped", "started_at": _now(),
                           "completed_at": _now(), "duration_ms": 0, "result": reason})


def result(agent: str, status: str, answer: str, sources: list | None = None, **metadata: Any) -> dict:
    """status: completed | partial | failed | unsupported | needs_input."""
    return {"agent": agent, "status": status, "answer": answer, "sources": sources or [], "metadata": metadata}


def error_info(error: Exception) -> dict:
    return {"code": getattr(error, "code", "agent_error"), "message": getattr(error, "message", None) or str(error),
            "status_code": getattr(error, "status_code", 500),
            "retry_after_seconds": getattr(error, "retry_after", None)}
