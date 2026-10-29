"""add embeddings to code chunks

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02

Adds nullable columns only: existing chunks are kept and simply start without an
embedding. The vector size is fixed here on purpose (migrations must not change when
application code changes later): 768 = gemini-embedding-001 with outputDimensionality=768.
The similarity-search index (HNSW) is added together with semantic search.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DIMENSIONS = 768


def _require_pgvector() -> None:
    if context.is_offline_mode():
        return
    if not op.get_bind().execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar():
        raise RuntimeError(
            "The pgvector extension is not enabled in this database, but embeddings need it. "
            "Install pgvector, then run as a superuser:  CREATE EXTENSION vector;  and retry."
        )


def upgrade() -> None:
    _require_pgvector()
    op.add_column("code_chunks", sa.Column("embedding", Vector(DIMENSIONS), nullable=True))
    op.add_column("code_chunks", sa.Column("embedding_model", sa.String(length=100), nullable=True))
    op.add_column("code_chunks", sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Discards all stored embeddings (the chunks themselves are kept).
    op.drop_column("code_chunks", "embedded_at")
    op.drop_column("code_chunks", "embedding_model")
    op.drop_column("code_chunks", "embedding")
