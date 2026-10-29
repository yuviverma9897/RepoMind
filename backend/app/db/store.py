"""All database queries. Functions take an open Session; the caller owns the transaction."""

from sqlalchemy import bindparam, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import CodeChunk, Repository

_BATCH = 1000


def upsert_repository(session: Session, meta: dict) -> Repository:
    """Insert or update a repository by its GitHub id.

    The upserted row stays locked until the transaction ends, so two analyses
    of the same repository run one after the other instead of interleaving.
    """
    values = {
        "github_id": meta["github_id"],
        "owner": meta["owner"],
        "name": meta["name"],
        "full_name": meta["full_name"],
        "github_url": meta["html_url"],
        "default_branch": meta["default_branch"],
        "description": meta.get("description"),
        "language": meta.get("language"),
        "stars": meta.get("stars") or 0,
        "forks": meta.get("forks") or 0,
    }
    dialect_name = session.bind.dialect.name if session.bind else "postgresql"
    if dialect_name == "postgresql":
        stmt = pg_insert(Repository).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Repository.github_id],
            set_={**{k: stmt.excluded[k] for k in values if k != "github_id"}, "updated_at": func.now()},
        ).returning(Repository.id)
        repository_id = session.execute(stmt).scalar_one()
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        stmt = sqlite_insert(Repository).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Repository.github_id],
            set_={**{k: stmt.excluded[k] for k in values if k != "github_id"}, "updated_at": func.now()},
        ).returning(Repository.id)
        repository_id = session.execute(stmt).scalar_one()
    return session.get(Repository, repository_id, populate_existing=True)


def get_repository(session: Session, repository_id: int) -> Repository | None:
    return session.get(Repository, repository_id)


def find_repository(session: Session, owner: str, name: str) -> Repository | None:
    full_name = f"{owner}/{name}".lower()
    return session.scalars(select(Repository).where(func.lower(Repository.full_name) == full_name)).first()


def file_hashes(session: Session, repository_id: int) -> dict[str, str]:
    """file_path -> file_hash for every file that currently has stored chunks."""
    rows = session.execute(
        select(CodeChunk.file_path, CodeChunk.file_hash).where(CodeChunk.repository_id == repository_id).distinct()
    )
    return {path: digest for path, digest in rows}


def delete_file_chunks(session: Session, repository_id: int, paths: list[str]) -> int:
    deleted = 0
    for i in range(0, len(paths), _BATCH):
        result = session.execute(
            delete(CodeChunk).where(
                CodeChunk.repository_id == repository_id, CodeChunk.file_path.in_(paths[i : i + _BATCH])
            )
        )
        deleted += result.rowcount
    return deleted


def insert_chunks(session: Session, rows: list[dict]) -> int:
    for i in range(0, len(rows), _BATCH):
        session.execute(insert(CodeChunk), rows[i : i + _BATCH])
    return len(rows)


def count_chunks(session: Session, repository_id: int, file_path: str | None = None) -> int:
    stmt = select(func.count()).select_from(CodeChunk).where(CodeChunk.repository_id == repository_id)
    if file_path:
        stmt = stmt.where(CodeChunk.file_path == file_path)
    return session.scalar(stmt)


def list_chunks(
    session: Session, repository_id: int, *, page: int, page_size: int, file_path: str | None = None
) -> list[CodeChunk]:
    stmt = select(CodeChunk).where(CodeChunk.repository_id == repository_id)
    if file_path:
        stmt = stmt.where(CodeChunk.file_path == file_path)
    stmt = stmt.order_by(CodeChunk.file_path, CodeChunk.start_line, CodeChunk.id)
    return list(session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))


# ---------------------------------------------------------------- embeddings

def embedding_column_dimensions(session: Session) -> int | None:
    """Size of code_chunks.embedding as defined in the database (None if the column is missing)."""
    dialect_name = session.bind.dialect.name if session.bind else "postgresql"
    if dialect_name == "postgresql":
        try:
            return session.execute(
                text("SELECT atttypmod FROM pg_attribute WHERE attrelid = 'code_chunks'::regclass "
                     "AND attname = 'embedding' AND NOT attisdropped")
            ).scalar()
        except Exception:
            return 768
    return 768


def pending_chunks(session: Session, repository_id: int, model: str, limit: int) -> list:
    """Next chunks without an embedding from `model` (never embedded, or embedded by another model)."""
    stmt = (
        select(CodeChunk.id, CodeChunk.file_path, CodeChunk.language, CodeChunk.chunk_type, CodeChunk.symbol_name,
               CodeChunk.parent_symbol, CodeChunk.part, CodeChunk.total_parts, CodeChunk.content)
        .where(CodeChunk.repository_id == repository_id, CodeChunk.embedding_model.is_distinct_from(model))
        .order_by(CodeChunk.id)
        .limit(limit)
    )
    return list(session.execute(stmt))


def save_embeddings(session: Session, items: list[tuple[int, list[float] | None]], model: str) -> int:
    """Store vectors by chunk id. A None vector marks the chunk as skipped (nothing to embed)."""
    if not items:
        return 0
    stmt = (
        update(CodeChunk.__table__)
        .where(CodeChunk.__table__.c.id == bindparam("chunk_id"))
        .values(embedding=bindparam("vector", type_=CodeChunk.__table__.c.embedding.type),
                embedding_model=model, embedded_at=func.now())
    )
    result = session.execute(stmt, [{"chunk_id": chunk_id, "vector": vector} for chunk_id, vector in items])
    return result.rowcount  # chunks deleted meanwhile (re-analysis) are simply not counted


def embedding_counts(session: Session, repository_id: int, model: str) -> dict:
    current = CodeChunk.embedding_model == model
    row = session.execute(
        select(
            func.count().label("total"),
            func.count().filter(current, CodeChunk.embedding.is_not(None)).label("embedded"),
            func.count().filter(current, CodeChunk.embedding.is_(None)).label("skipped"),
            func.count().filter(CodeChunk.embedding_model.is_not(None), ~current).label("outdated"),
            func.max(CodeChunk.embedded_at).filter(current).label("last_embedded_at"),
        ).where(CodeChunk.repository_id == repository_id)
    ).one()
    return row._asdict()


# ---------------------------------------------------------------- semantic search

def search_chunks(session: Session, repository_id: int, model: str, vector: list[float], limit: int) -> list:
    """Exact nearest-neighbour search by cosine distance, restricted to one repository and one model."""
    dialect_name = session.bind.dialect.name if session.bind else "postgresql"
    if dialect_name == "postgresql":
        distance = CodeChunk.embedding.cosine_distance(vector)
        stmt = (
            select(CodeChunk.id, CodeChunk.repository_id, CodeChunk.file_path, CodeChunk.language, CodeChunk.chunk_type,
                   CodeChunk.symbol_name, CodeChunk.parent_symbol, CodeChunk.start_line, CodeChunk.end_line,
                   CodeChunk.part, CodeChunk.total_parts, CodeChunk.content, (1 - distance).label("similarity"))
            .where(CodeChunk.repository_id == repository_id, CodeChunk.embedding_model == model,
                   CodeChunk.embedding.is_not(None))
            .order_by(distance)
            .limit(limit)
        )
        return list(session.execute(stmt))
    else:
        # SQLite: in-process exact cosine similarity search
        import json
        import math
        from collections import namedtuple

        stmt = (
            select(CodeChunk.id, CodeChunk.repository_id, CodeChunk.file_path, CodeChunk.language, CodeChunk.chunk_type,
                   CodeChunk.symbol_name, CodeChunk.parent_symbol, CodeChunk.start_line, CodeChunk.end_line,
                   CodeChunk.part, CodeChunk.total_parts, CodeChunk.content, CodeChunk.embedding)
            .where(CodeChunk.repository_id == repository_id, CodeChunk.embedding_model == model,
                   CodeChunk.embedding.is_not(None))
        )
        candidates = list(session.execute(stmt))
        scored = []
        norm_q = math.sqrt(sum(x * x for x in vector))
        for c in candidates:
            emb = c.embedding
            if isinstance(emb, str):
                try:
                    emb = json.loads(emb)
                except Exception:
                    emb = None
            if not emb or not isinstance(emb, (list, tuple)):
                continue
            dot = sum(x * y for x, y in zip(vector, emb))
            norm_c = math.sqrt(sum(x * x for x in emb))
            sim = dot / (norm_q * norm_c) if (norm_q and norm_c) else 0.0
            scored.append((c, sim))

        scored.sort(key=lambda item: item[1], reverse=True)
        ChunkRow = namedtuple("ChunkRow", [
            "id", "repository_id", "file_path", "language", "chunk_type",
            "symbol_name", "parent_symbol", "start_line", "end_line",
            "part", "total_parts", "content", "similarity"
        ])
        results = []
        for c, sim in scored[:limit]:
            results.append(ChunkRow(
                id=c.id, repository_id=c.repository_id, file_path=c.file_path, language=c.language,
                chunk_type=c.chunk_type, symbol_name=c.symbol_name, parent_symbol=c.parent_symbol,
                start_line=c.start_line, end_line=c.end_line, part=c.part, total_parts=c.total_parts,
                content=c.content, similarity=sim
            ))
        return results


# ---------------------------------------------------------------- reading stored code (Phases 10-12)

def file_chunks(session: Session, repository_id: int, file_path: str) -> list:
    stmt = (
        select(CodeChunk.id, CodeChunk.file_path, CodeChunk.language, CodeChunk.chunk_type, CodeChunk.symbol_name,
               CodeChunk.parent_symbol, CodeChunk.start_line, CodeChunk.end_line, CodeChunk.part,
               CodeChunk.total_parts, CodeChunk.content)
        .where(CodeChunk.repository_id == repository_id, CodeChunk.file_path == file_path)
        .order_by(CodeChunk.start_line, CodeChunk.part, CodeChunk.id)
    )
    return list(session.execute(stmt))


def symbol_chunks(session: Session, repository_id: int, names: list[str], limit: int) -> list:
    """Chunks that define any of `names` (first part only), for "definitions of called functions"."""
    if not names:
        return []
    stmt = (
        select(CodeChunk.id, CodeChunk.file_path, CodeChunk.language, CodeChunk.chunk_type, CodeChunk.symbol_name,
               CodeChunk.parent_symbol, CodeChunk.start_line, CodeChunk.end_line, CodeChunk.part,
               CodeChunk.total_parts, CodeChunk.content)
        .where(CodeChunk.repository_id == repository_id, CodeChunk.symbol_name.in_(names),
               (CodeChunk.part.is_(None)) | (CodeChunk.part == 1))
        .order_by(CodeChunk.file_path, CodeChunk.start_line)
        .limit(limit)
    )
    return list(session.execute(stmt))


def iter_chunks(session: Session, repository_id: int, *, languages: set[str] | None = None,
                path_prefix: str | None = None, batch: int = 1000):
    """Stream lightweight chunk rows for whole-repository scans (imports, security rules)."""
    stmt = (
        select(CodeChunk.id, CodeChunk.file_path, CodeChunk.language, CodeChunk.chunk_type, CodeChunk.symbol_name,
               CodeChunk.parent_symbol, CodeChunk.start_line, CodeChunk.end_line, CodeChunk.content)
        .where(CodeChunk.repository_id == repository_id)
        .order_by(CodeChunk.file_path, CodeChunk.start_line, CodeChunk.id)
        .execution_options(yield_per=batch)
    )
    if languages:
        stmt = stmt.where(CodeChunk.language.in_(languages))
    if path_prefix:
        stmt = stmt.where(CodeChunk.file_path.startswith(path_prefix, autoescape=True))
    yield from session.execute(stmt)


def file_languages(session: Session, repository_id: int) -> dict[str, str]:
    """{file_path: language} for every stored file."""
    stmt = select(CodeChunk.file_path, CodeChunk.language).where(CodeChunk.repository_id == repository_id).distinct()
    return dict(session.execute(stmt).all())


def chunks_containing(session: Session, repository_id: int, text_: str, limit: int) -> list:
    """Chunks whose content contains `text_` (plain substring, case-sensitive). Callers refine with a regex."""
    stmt = (
        select(CodeChunk.file_path, CodeChunk.language, CodeChunk.start_line, CodeChunk.end_line, CodeChunk.content)
        .where(CodeChunk.repository_id == repository_id, CodeChunk.content.contains(text_, autoescape=True))
        .order_by(CodeChunk.file_path, CodeChunk.start_line)
        .limit(limit)
    )
    return list(session.execute(stmt))
