"""Database tables (SQLAlchemy ORM). Change these together with an Alembic migration."""

from datetime import datetime

# Size of code_chunks.embedding. Changing it requires a new migration (and re-embedding);
# config.EMBEDDING_DIMENSIONS must match it.
EMBEDDING_DIMENSIONS = 768

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, WriteOnlyMapped, mapped_column, relationship


class Base(DeclarativeBase):
    # Predictable constraint names, so migrations can refer to them on every machine.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Repository(TimestampMixin, Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True, autoincrement=True)
    github_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), unique=True)  # GitHub's id survives renames/transfers
    owner: Mapped[str] = mapped_column(String(39))
    name: Mapped[str] = mapped_column(String(100))
    full_name: Mapped[str] = mapped_column(String(140))
    github_url: Mapped[str] = mapped_column(String(300))
    default_branch: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(64))
    stars: Mapped[int] = mapped_column(Integer, server_default="0")
    forks: Mapped[int] = mapped_column(Integer, server_default="0")

    # State of the stored index (all NULL until the first successful analysis).
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexed_branch: Mapped[str | None] = mapped_column(String(255))
    indexed_commit_sha: Mapped[str | None] = mapped_column(String(40))
    index_config: Mapped[str | None] = mapped_column(String(64))  # chunker version + chunk settings used
    index_stats: Mapped[dict | None] = mapped_column(JSONB().with_variant(JSON, "sqlite"))  # summary of the last analysis

    # Write-only: a repository can have tens of thousands of chunks, so they are
    # never loaded implicitly. Query them with repository.chunks.select().
    chunks: WriteOnlyMapped["CodeChunk"] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # Case-insensitive lookup by "owner/name" (GitHub names are case-insensitive).
        Index("ix_repositories_full_name_lower", text("lower(full_name)")),
    )


class CodeChunk(TimestampMixin, Base):
    __tablename__ = "code_chunks"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("repositories.id", ondelete="CASCADE"))
    chunk_key: Mapped[str] = mapped_column(String(24))  # deterministic id from the chunker
    file_path: Mapped[str] = mapped_column(Text)
    file_hash: Mapped[str] = mapped_column(String(64))  # sha256 of the whole file
    language: Mapped[str] = mapped_column(String(32))
    chunk_type: Mapped[str] = mapped_column(String(32))
    symbol_name: Mapped[str | None] = mapped_column(Text)
    parent_symbol: Mapped[str | None] = mapped_column(Text)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    part: Mapped[int | None] = mapped_column(Integer)
    total_parts: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    # Semantic vector of the chunk (NULL until embedded). embedding_model records which
    # model produced it, so switching models marks old vectors as outdated.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS).with_variant(JSON, "sqlite"))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    repository: Mapped[Repository] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("repository_id", "chunk_key"),  # no duplicate chunks per repository
        Index("ix_code_chunks_repository_file", "repository_id", "file_path", "start_line"),
        CheckConstraint("start_line >= 1 AND end_line >= start_line", name="valid_line_range"),
    )
