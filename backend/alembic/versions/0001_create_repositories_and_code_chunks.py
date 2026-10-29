"""create repositories and code chunks, enable pgvector

Revision ID: 0001
Revises: 
Create Date: 2026-10-02 08:35:57.591036

"""
from typing import Sequence, Union

import logging

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

log = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_pgvector() -> None:
    """Enable pgvector if the server has it and we are allowed to.

    Embeddings (Phase 6) need it; the tables below do not. A missing extension
    therefore only produces a warning here, and /api/health reports it.
    CREATE EXTENSION usually needs a superuser, so the recommended setup is to
    run it once as the postgres user (see README); then this is a no-op.
    """
    if context.is_offline_mode():  # generating a SQL script: no database to inspect
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        return
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar():
        return
    if not bind.execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).scalar():
        log.warning("pgvector is not installed on this PostgreSQL server; semantic search will need it (see README).")
        return
    try:
        with bind.begin_nested():  # savepoint: a permission error must not abort the migration
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    except sa.exc.DBAPIError:
        log.warning("Could not create the pgvector extension (insufficient privilege). As a superuser run: "
                    "CREATE EXTENSION vector;  in this database.")


def upgrade() -> None:
    _enable_pgvector()

    op.create_table('repositories',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('github_id', sa.BigInteger(), nullable=False),
    sa.Column('owner', sa.String(length=39), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('full_name', sa.String(length=140), nullable=False),
    sa.Column('github_url', sa.String(length=300), nullable=False),
    sa.Column('default_branch', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('language', sa.String(length=64), nullable=True),
    sa.Column('stars', sa.Integer(), server_default='0', nullable=False),
    sa.Column('forks', sa.Integer(), server_default='0', nullable=False),
    sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('indexed_branch', sa.String(length=255), nullable=True),
    sa.Column('indexed_commit_sha', sa.String(length=40), nullable=True),
    sa.Column('index_config', sa.String(length=64), nullable=True),
    sa.Column('index_stats', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_repositories')),
    sa.UniqueConstraint('github_id', name=op.f('uq_repositories_github_id'))
    )
    op.create_index('ix_repositories_full_name_lower', 'repositories', [sa.literal_column('lower(full_name)')], unique=False)
    op.create_table('code_chunks',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('repository_id', sa.BigInteger(), nullable=False),
    sa.Column('chunk_key', sa.String(length=24), nullable=False),
    sa.Column('file_path', sa.Text(), nullable=False),
    sa.Column('file_hash', sa.String(length=64), nullable=False),
    sa.Column('language', sa.String(length=32), nullable=False),
    sa.Column('chunk_type', sa.String(length=32), nullable=False),
    sa.Column('symbol_name', sa.Text(), nullable=True),
    sa.Column('parent_symbol', sa.Text(), nullable=True),
    sa.Column('start_line', sa.Integer(), nullable=False),
    sa.Column('end_line', sa.Integer(), nullable=False),
    sa.Column('part', sa.Integer(), nullable=True),
    sa.Column('total_parts', sa.Integer(), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('start_line >= 1 AND end_line >= start_line', name=op.f('ck_code_chunks_valid_line_range')),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], name=op.f('fk_code_chunks_repository_id_repositories'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_code_chunks')),
    sa.UniqueConstraint('repository_id', 'chunk_key', name=op.f('uq_code_chunks_repository_id_chunk_key'))
    )
    op.create_index('ix_code_chunks_repository_file', 'code_chunks', ['repository_id', 'file_path', 'start_line'], unique=False)


def downgrade() -> None:
    # The pgvector extension is left installed: other database objects may use it,
    # and dropping it requires superuser rights.
    op.drop_index('ix_code_chunks_repository_file', table_name='code_chunks')
    op.drop_table('code_chunks')
    op.drop_index('ix_repositories_full_name_lower', table_name='repositories')
    op.drop_table('repositories')
