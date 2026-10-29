"""Alembic migration environment.

The connection string comes from DATABASE_URL (app/config.py), never from
alembic.ini, so credentials stay out of version control. Tests may pass a
different URL through config.attributes["database_url"].
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app import config as app_config
from app.db.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = config.attributes.get("database_url") or app_config.DATABASE_URL
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add it to backend/.env (see .env.example).")
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it:  alembic upgrade head --sql"""
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
