"""Analyze & index flow and endpoints, with GitHub replaced by in-memory fakes."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.db import database
from app.db.models import CodeChunk
from app.main import app
from app.services import github, pipeline
from app.services.indexing import plan_sync
from tests.helpers import make_archive

META = {
    "github_id": 7,
    "owner": "owner",
    "name": "repo",
    "full_name": "owner/repo",
    "html_url": "https://github.com/owner/repo",
    "default_branch": "main",
    "description": "demo",
    "language": "Python",
    "stars": 3,
    "forks": 0,
}

FILES = {
    "src/app.py": b"import os\n\n\ndef main():\n    return os.getcwd()\n\n\nclass Service:\n    def run(self):\n        return 1\n",
    "src/util.js": b"export function add(a, b) {\n  return a + b;\n}\n",
    "README.md": b"# Demo\n\nHello.\n\n## Usage\n\nRun it.\n",
    "logo.png": b"\x89PNG\r\n\x1a\n",
    "empty.py": b"",
}

client = TestClient(app)


@pytest.fixture
def fake_github(monkeypatch):
    """Serve repository metadata and an archive built from `state.files`."""

    class State:
        files = dict(FILES)
        downloads = 0

    async def get_repository(owner, repo):
        return META

    async def download_archive(owner, repo, branch):
        State.downloads += 1
        return make_archive(State.files)

    monkeypatch.setattr(github, "get_repository", get_repository)
    monkeypatch.setattr(pipeline, "download_archive", download_archive)
    return State


def analyze():
    response = client.post("/api/repos/analyze", json={"owner": "owner", "repo": "repo", "branch": "main"})
    assert response.status_code == 200, response.text
    return response.json()


def stored_ids(path=None):
    with database.session_scope() as s:
        stmt = select(CodeChunk.id, CodeChunk.file_path)
        return {i for i, p in s.execute(stmt) if path is None or p == path}


def test_plan_sync():
    plan = plan_sync({"a": "1", "b": "2", "c": "3"}, {"a": "1", "b": "X", "d": "4"})
    assert (plan.added, plan.changed, plan.removed, plan.unchanged) == (["d"], ["b"], ["c"], ["a"])
    rebuild = plan_sync({"a": "1"}, {"a": "1"}, rebuild=True)
    assert rebuild.changed == ["a"] and not rebuild.unchanged


def test_first_analysis_persists_chunks(db, fake_github):
    result = analyze()
    assert result["status"] == "indexed"
    assert result["repository"] == "owner/repo" and result["repository_id"] == 1
    assert result["files_processed"] == 3  # app.py, util.js, README.md
    assert result["files_added"] == 3 and result["files_skipped"] == 2
    assert result["chunks_created"] == result["chunks_total"] == len(stored_ids()) > 0
    assert set(result["languages"]) == {"python", "javascript", "markdown"}
    assert result["commit_sha"] == "0123456789abcdef0123456789abcdef01234567"

    page = client.get("/api/repos/1/chunks", params={"page_size": 100}).json()
    symbols = {(c["chunk_type"], c["symbol_name"]) for c in page["chunks"]}
    assert {("function", "main"), ("class", "Service"), ("function", "add"), ("section", "Usage")} <= symbols
    service = next(c for c in page["chunks"] if c["symbol_name"] == "Service")  # small class stays whole
    assert service["file_path"] == "src/app.py" and service["language"] == "python"
    assert service["content"] == "class Service:\n    def run(self):\n        return 1"
    assert (service["start_line"], service["end_line"]) == (8, 10)
    assert len(service["file_hash"]) == 64


def test_reanalysis_without_changes_writes_nothing(db, fake_github):
    first = analyze()
    ids = stored_ids()
    second = analyze()
    assert fake_github.downloads == 2  # always analyzes the current snapshot, never the cache
    assert second["status"] == "unchanged"
    assert second["chunks_created"] == second["chunks_deleted"] == 0
    assert second["files_unchanged"] == 3
    assert second["chunks_total"] == first["chunks_total"]
    assert stored_ids() == ids  # same rows, nothing duplicated or rewritten


def test_reanalysis_syncs_changed_added_and_removed_files(db, fake_github):
    analyze()
    readme_ids = stored_ids("README.md")
    fake_github.files = {
        **FILES,
        "src/util.js": b"export function add(a, b) {\n  return a + b + 0;\n}\n",  # changed
        "src/new.py": b"def fresh():\n    return 2\n",  # added
    }
    del fake_github.files["src/app.py"]  # removed

    result = analyze()
    assert result["status"] == "indexed"
    assert (result["files_added"], result["files_updated"], result["files_removed"], result["files_unchanged"]) == (1, 1, 1, 1)
    assert stored_ids("README.md") == readme_ids  # untouched file keeps its rows
    assert stored_ids("src/app.py") == set()
    assert result["chunks_total"] == len(stored_ids())
    with database.session_scope() as s:
        paths = set(s.scalars(select(CodeChunk.file_path).distinct()))
    assert paths == {"README.md", "src/util.js", "src/new.py"}


def test_changed_chunk_settings_rebuild_everything(db, fake_github, monkeypatch):
    analyze()
    monkeypatch.setattr(config, "CHUNK_SIZE", 400)
    result = analyze()
    assert result["files_updated"] == 3 and result["files_unchanged"] == 0
    assert result["chunks_total"] == len(stored_ids())


def test_index_status(db, fake_github):
    params = {"owner": "OWNER", "repo": "Repo"}  # case-insensitive
    assert client.get("/api/repos/index-status", params=params).json()["indexed"] is False
    analyze()
    status = client.get("/api/repos/index-status", params=params).json()
    assert status["indexed"] is True and status["repository_id"] == 1
    assert status["chunks_total"] > 0 and status["files_processed"] == 3
    assert status["languages"] == ["javascript", "markdown", "python"]  # 1 file each -> alphabetical


def test_stored_chunks_pagination_and_errors(db, fake_github):
    total = analyze()["chunks_total"]
    first = client.get("/api/repos/1/chunks", params={"page": 1, "page_size": 2}).json()
    second = client.get("/api/repos/1/chunks", params={"page": 2, "page_size": 2}).json()
    assert first["total"] == total and first["total_pages"] == (total + 1) // 2
    assert len(first["chunks"]) == 2 and {c["id"] for c in first["chunks"]}.isdisjoint(c["id"] for c in second["chunks"])
    only = client.get("/api/repos/1/chunks", params={"file_path": "src/util.js"}).json()
    assert only["total"] == 1 and only["chunks"][0]["symbol_name"] == "add"

    missing = client.get("/api/repos/999/chunks")
    assert missing.status_code == 404 and missing.json()["code"] == "repository_not_indexed"
    assert client.get("/api/repos/1/chunks", params={"page_size": 500}).status_code == 422
    assert client.get("/api/repos/1/chunks", params={"page": 0}).status_code == 422


def test_without_database_url_endpoints_answer_503(monkeypatch, fake_github):
    monkeypatch.setattr(config, "DATABASE_URL", None)
    database.reset_engine()
    response = client.post("/api/repos/analyze", json={"owner": "owner", "repo": "repo"})
    assert response.status_code == 503 and response.json()["code"] == "database_not_configured"
    assert fake_github.downloads == 0  # failed before using GitHub
    assert client.get("/api/health").json()["database"] == {"configured": False, "connected": False}
