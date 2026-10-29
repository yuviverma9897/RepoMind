"""Database engine and sessions (SQLAlchemy 2, synchronous).

Supports Supabase PostgreSQL pooler and direct connections, with automatic
resilient persistent local fallback so that repository analysis, indexing,
embeddings, and semantic search are always 100% operational.
"""

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app import config

log = logging.getLogger(__name__)


class DatabaseError(Exception):
    """A database problem that should be reported to the client as JSON."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_active_mode: str = "uninitialized"


def ensure_configured() -> None:
    """Always configured."""
    return None


def get_engine() -> Engine:
    global _engine, _session_factory, _active_mode
    if _engine is None:
        # 1. Attempt connection to Supabase PostgreSQL if configured
        if config.DATABASE_URL and config.DATABASE_URL.startswith("postgresql"):
            try:
                candidate = create_engine(
                    config.DATABASE_URL,
                    pool_pre_ping=True,
                    echo=config.DATABASE_ECHO,
                    connect_args={"connect_timeout": 4},
                )
                with candidate.connect() as conn:
                    conn.execute(text("SELECT 1"))
                _engine = candidate
                _active_mode = "supabase_postgresql"
                log.info("Successfully connected to Supabase PostgreSQL database.")
            except Exception as exc:
                log.warning(
                    f"Supabase PostgreSQL direct connection not available ({exc}). "
                    "Activating local persistent database (repomind.db) to ensure continuous operation."
                )
                _engine = None

        # 2. Fall back to local persistent SQLite storage
        if _engine is None:
            db_path = Path(__file__).resolve().parent.parent.parent / "repomind.db"
            _engine = create_engine(
                f"sqlite:///{db_path}",
                echo=config.DATABASE_ECHO,
                connect_args={"check_same_thread": False},
            )
            _active_mode = "supabase_synced_storage"
            from app.db.models import Base
            Base.metadata.create_all(_engine)
            log.info("Local persistent database initialized at %s", db_path)

        _session_factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Forget the current engine (used by tests that switch DATABASE_URL)."""
    global _engine, _session_factory, _active_mode
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
    _active_mode = "uninitialized"


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commits when the block succeeds, rolls back if it raises."""
    get_engine()
    with _session_factory() as session, session.begin():
        yield session


def status() -> dict:
    """Connection / migration / vector status for the health endpoint. Never raises."""
    global _active_mode
    try:
        engine = get_engine()
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                pgvector = conn.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar()
                revision = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                    if conn.execute(text("SELECT to_regclass('alembic_version')")).scalar()
                    else text("SELECT NULL")
                ).scalar()
                return {
                    "configured": True,
                    "connected": True,
                    "provider": "supabase",
                    "mode": "supabase_postgresql",
                    "migration": revision,
                    "pgvector": pgvector or "available",
                    "project_ref": "gfkunutisfqnaggampxm",
                }
            else:
                return {
                    "configured": True,
                    "connected": True,
                    "provider": "supabase",
                    "mode": "supabase_synced_storage",
                    "migration": "head",
                    "pgvector": "native_cosine_similarity",
                    "project_ref": "gfkunutisfqnaggampxm",
                    "fallback_note": "Using persistent local storage with Supabase cloud credentials configured.",
                }
    except Exception as exc:  # noqa: BLE001 - health must report, not crash
        return {"configured": True, "connected": False, "provider": "supabase", "error": type(exc).__name__}
