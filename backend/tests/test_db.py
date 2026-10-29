"""Schema, migration, constraint and query tests against a real PostgreSQL database."""

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db import database, store
from app.db.models import CodeChunk, Repository
from tests.conftest import alembic_config

META = {
    "github_id": 1001,
    "owner": "Owner",
    "name": "Repo",
    "full_name": "Owner/Repo",
    "html_url": "https://github.com/Owner/Repo",
    "default_branch": "main",
    "description": "A test repository",
    "language": "Python",
    "stars": 5,
    "forks": 1,
}


def chunk_row(repository_id, n, path="src/app.py", **overrides):
    row = {
        "repository_id": repository_id,
        "chunk_key": f"key{n:021d}",
        "file_path": path,
        "file_hash": "a" * 64,
        "language": "python",
        "chunk_type": "function",
        "symbol_name": f"f{n}",
        "parent_symbol": None,
        "start_line": n * 10 + 1,
        "end_line": n * 10 + 5,
        "part": None,
        "total_parts": None,
        "content": f"def f{n}():\n    pass",
    }
    return {**row, **overrides}


def test_connection(db):
    with db.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
        assert conn.execute(text("SHOW server_version")).scalar()


def test_migrations_are_applied_and_match_the_models(db):
    head = ScriptDirectory.from_config(alembic_config("unused")).get_current_head()
    with db.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
    inspector = inspect(db)
    assert {"repositories", "code_chunks"} <= set(inspector.get_table_names())
    columns = {c["name"] for c in inspector.get_columns("code_chunks")}
    assert {"repository_id", "file_path", "file_hash", "chunk_type", "symbol_name", "start_line", "end_line",
            "content", "created_at", "updated_at"} <= columns
    fks = inspector.get_foreign_keys("code_chunks")
    assert fks[0]["referred_table"] == "repositories" and fks[0]["options"]["ondelete"] == "CASCADE"


def test_pgvector_extension_is_available_and_works(db):
    with db.connect() as conn:
        assert conn.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar()
        # Distance between two literal vectors: proves the type and operators work, no embeddings needed.
        assert conn.execute(text("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector")).scalar() == 1.0


def test_health_reports_database_status(db):
    status = database.status()
    assert status["connected"] and status["migration"] and status["pgvector"]


def test_repository_upsert_inserts_once_and_updates(db):
    with database.session_scope() as s:
        first = store.upsert_repository(s, META)
    with database.session_scope() as s:
        second = store.upsert_repository(s, {**META, "stars": 99, "full_name": "Owner/Renamed", "name": "Renamed"})
        assert second.id == first.id
        assert (second.stars, second.full_name) == (99, "Owner/Renamed")
        assert s.scalar(select(func.count()).select_from(Repository)) == 1
        assert store.find_repository(s, "owner", "renamed").id == first.id  # case-insensitive lookup


def test_chunk_insertion_and_relationship(db):
    with database.session_scope() as s:
        repo = store.upsert_repository(s, META)
        other = store.upsert_repository(s, {**META, "github_id": 2002, "full_name": "x/y"})
        store.insert_chunks(s, [chunk_row(repo.id, n) for n in range(3)] + [chunk_row(other.id, 0)])
        repo_id = repo.id

    with database.session_scope() as s:
        repo = s.get(Repository, repo_id)
        chunks = s.scalars(repo.chunks.select().order_by(CodeChunk.start_line)).all()
        assert [c.symbol_name for c in chunks] == ["f0", "f1", "f2"]
        assert all(c.repository.full_name == "Owner/Repo" for c in chunks)
        assert chunks[0].created_at is not None

        s.execute(delete(Repository).where(Repository.id == repo_id))  # cascade to its chunks only
        assert store.count_chunks(s, repo_id) == 0
        assert s.scalar(select(func.count()).select_from(CodeChunk)) == 1


def test_duplicate_chunk_key_is_rejected_by_the_database(db):
    with pytest.raises(IntegrityError, match="uq_code_chunks_repository_id_chunk_key"):
        with database.session_scope() as s:
            repo = store.upsert_repository(s, META)
            store.insert_chunks(s, [chunk_row(repo.id, 1), chunk_row(repo.id, 1)])


def test_invalid_line_range_is_rejected(db):
    with pytest.raises(IntegrityError, match="valid_line_range"):
        with database.session_scope() as s:
            repo = store.upsert_repository(s, META)
            store.insert_chunks(s, [chunk_row(repo.id, 1, start_line=10, end_line=3)])


def test_pagination_is_ordered_and_complete(db):
    with database.session_scope() as s:
        repo = store.upsert_repository(s, META)
        rows = [chunk_row(repo.id, n, path=f"src/{'b' if n % 2 else 'a'}.py") for n in range(45)]
        store.insert_chunks(s, rows)
        repo_id = repo.id

    with database.session_scope() as s:
        pages = [store.list_chunks(s, repo_id, page=p, page_size=20) for p in (1, 2, 3, 4)]
        assert [len(p) for p in pages] == [20, 20, 5, 0]
        seen = [(c.file_path, c.start_line) for page in pages for c in page]
        assert seen == sorted(seen) and len(set(seen)) == 45
        assert store.count_chunks(s, repo_id, "src/a.py") == 23
        assert all(c.file_path == "src/b.py" for c in store.list_chunks(s, repo_id, page=1, page_size=50, file_path="src/b.py"))
