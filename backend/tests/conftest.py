"""Database fixtures.

Database tests run against TEST_DATABASE_URL, a separate database that the tests
wipe and re-migrate. They are skipped when it is not set. They refuse to run if it
points to the same database as DATABASE_URL, so development data is never touched.
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app import config
from app.db import database

TEST_DATABASE_URL = config._sqlalchemy_url(os.getenv("TEST_DATABASE_URL", "").strip())
BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logging"] = False
    return cfg


@pytest.fixture(scope="session")
def migrated_test_database() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    if TEST_DATABASE_URL == config.DATABASE_URL:
        pytest.exit("TEST_DATABASE_URL must be a separate database: the tests delete its data.", returncode=2)
    cfg = alembic_config(TEST_DATABASE_URL)
    command.downgrade(cfg, "base")  # proves the downgrade works, and starts from scratch
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture
def db(migrated_test_database, monkeypatch):
    """Point the app at the (empty) test database for one test."""
    monkeypatch.setattr(config, "DATABASE_URL", migrated_test_database)
    database.reset_engine()
    with database.get_engine().begin() as conn:
        conn.execute(text("TRUNCATE code_chunks, repositories RESTART IDENTITY CASCADE"))
    yield database.get_engine()
    database.reset_engine()
