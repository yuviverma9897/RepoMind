"""Phase 13: repository-grounded documentation (stub LLM and GitHub; evidence gathering, DB and API are real)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.db import database, store
from app.db.models import CodeChunk
from app.main import app
from app.services import dependencies, documentation, embedding_service, github, llm
from app.services.llm import LLMError
from tests.helpers import index_files
from tests.test_rag import FakeLLM
from tests.test_search import QueryProvider, vec

client = TestClient(app)
AWS_KEY = "AKIA" + "Q3EXAMPLEKEY7Z2B"

README = """# Tasker

Tasker is a small task tracking web app.

## Installation

```bash
pip install -r requirements.txt
flask --app app run
```

## Usage

Open http://localhost:5000 and log in.
"""
APP_INIT = '''from flask import Flask
from . import routes, db


def create_app():
    app = Flask(__name__)
    db.init_app(app)
    app.register_blueprint(routes.bp)
    return app
'''
ROUTES = '''from flask import Blueprint
from .db import get_db

bp = Blueprint("tasks", __name__)


@bp.route("/")
def index():
    return get_db().list_tasks()


@bp.route("/login", methods=["GET", "POST"])
def login():
    return "login"
'''
DB = '''def get_db():
    return None


def init_app(app):
    app.teardown_appcontext(lambda e: None)
'''
TESTS = '''from flask import Flask
app = Flask(__name__)


@app.route("/only-in-tests")
def fake():
    return "x"
'''
FILES = {"README.md": README, "app/__init__.py": APP_INIT, "app/routes.py": ROUTES, "app/db.py": DB,
         "tests/test_app.py": TESTS}
GITHUB_FILES = {
    "requirements.txt": "flask>=3.0\npytest\n",
    "Dockerfile": "FROM python:3.12-slim\nRUN pip install -r requirements.txt\nCMD [\"flask\", \"run\"]\n",
    ".env.example": f"AWS_ACCESS_KEY_ID={AWS_KEY}\nSECRET_KEY=changeme\n",
    ".github/workflows/ci.yml": "jobs:\n  test:\n    steps:\n      - run: pytest\n",
}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    documentation._cache.clear()
    documentation._tree_cache.clear()
    dependencies._cache.clear()
    calls = {"tree": 0, "files": []}

    async def fake_import(url):
        calls["tree"] += 1
        return {"files": [{"path": p, "type": "file"} for p in [*FILES, *GITHUB_FILES]]}

    async def fake_file(owner, repo, branch, path):
        calls["files"].append(path)
        content = GITHUB_FILES[path]
        return {"path": path, "name": path, "language": "text", "size": len(content), "kind": "text",
                "content": content, "message": None, "html_url": None}

    monkeypatch.setattr(github, "import_repository", fake_import)
    monkeypatch.setattr(github, "get_file_content", fake_file)
    return calls


@pytest.fixture
def model(monkeypatch):
    stub = FakeLLM("### Purpose\nTasker tracks tasks [S1]. Run it with `flask run` [S2]. Unknown [S9].")
    monkeypatch.setattr(llm, "get_llm", lambda: stub)
    return stub


@pytest.fixture
def repo(db):
    return index_files(701, "tasker", FILES)


def gen(repo_id, section, **body):
    return client.post(f"/api/repos/{repo_id}/docs/{section}", json=body)


def test_overview_is_grounded_cited_and_cached(repo, model):
    r = gen(repo, "overview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["title"], body["repository"]) == ("generated", "Repository Overview", "o/tasker")
    assert body["sources"][0]["file_path"] == "README.md" and body["sources"][0]["role"] == "readme"
    assert body["citations"] == {"cited": ["S1", "S2"], "invalid": ["S9"]} and "[S9]" not in body["content"]
    assert body["facts"]["languages"] == {"python": 4, "markdown": 1}
    assert "app/__init__.py" in body["facts"]["entry_points"] or body["facts"]["top_directories"]
    assert body["generated_at"] and body["model"] == "fake-llm" and body["cached"] is False
    system, prompt = model.calls[0]
    assert "Not found in the repository evidence" in system and "### Main features" in system
    assert "Tasker is a small task tracking web app." in prompt and "- languages: python 4, markdown 1" in prompt
    assert any("Semantic search was not used" in n for n in body["notes"])  # no embeddings in this repo
    # cached: no second model call, until regenerate
    assert gen(repo, "overview").json()["cached"] is True and len(model.calls) == 1
    assert gen(repo, "overview", regenerate=True).json()["cached"] is False and len(model.calls) == 2


def test_api_section_lists_routes_from_code_not_tests(repo, model):
    body = gen(repo, "api").json()
    routes = body["facts"]["routes"]
    assert "GET /" in routes[0] and any(r.startswith("GET,POST /login (flask) - app/routes.py:") for r in routes)
    assert not any("only-in-tests" in r for r in routes) and body["facts"]["routes_found"] == 2
    assert {s["role"] for s in body["sources"]} == {"route"}
    login = next(r for r in routes if "/login" in r)
    line = int(login.rsplit(":", 1)[1])
    assert ROUTES.splitlines()[line - 1].startswith('@bp.route("/login"')


def test_setup_uses_manifests_from_github_and_masks_secrets(repo, model, clean):
    body = gen(repo, "setup").json()
    assert body["status"] == "generated"
    roles = {(s["file_path"], s["role"]) for s in body["sources"]}
    assert {("requirements.txt", "manifest"), ("Dockerfile", "manifest"), (".env.example", "manifest")} <= roles
    assert any(s["role"] == "docs" and s["file_path"] == "README.md" for s in body["sources"])  # installation part
    assert "requirements.txt" in body["facts"]["relevant_files"]
    _, prompt = model.calls[0]
    assert "pip install -r requirements.txt" in prompt and AWS_KEY not in prompt and "AKIA****" in prompt
    assert AWS_KEY not in str(body)
    gen(repo, "developer_guide")
    assert clean["tree"] == 1  # the GitHub file list is fetched once per indexed version
    dev = documentation._cache[next(k for k in documentation._cache if k[2] == "developer_guide")]
    assert ".github/workflows/ci.yml" in dev["facts"]["relevant_files"] and dev["facts"]["test_directories"] == ["tests"]


def test_missing_documentation(db, model, monkeypatch):
    repo_id = index_files(702, "noreadme", {"src/main.py": APP_INIT, "src/util.py": DB})
    body = gen(repo_id, "overview").json()
    assert "No README was found among the indexed files." in body["notes"]
    assert body["status"] == "generated" and body["sources"][0]["file_path"] == "src/main.py"  # entry point instead

    async def broken(url):
        raise github.GitHubError(404, "repo_not_found", "Repository not found")
    monkeypatch.setattr(github, "import_repository", broken)
    setup = gen(repo_id, "setup").json()
    assert setup["status"] == "insufficient_evidence" and "Not enough evidence" in setup["content"]
    assert any("Could not read the file list from GitHub" in n for n in setup["notes"])
    assert len(model.calls) == 1  # no model call without evidence


def test_incomplete_structure(db, model):
    repo_id = index_files(703, "single", {"tool.py": "def helper(x):\n    return x * 2\n"})
    api = gen(repo_id, "api").json()
    assert api["status"] == "insufficient_evidence" and api["facts"]["routes_found"] == 0
    assert any("No HTTP routes were detected" in n for n in api["notes"])
    modules = gen(repo_id, "modules").json()
    assert modules["status"] == "insufficient_evidence" and model.calls == []
    structure = gen(repo_id, "structure").json()
    assert structure["facts"]["directories"] == ["(root) - 1 files (python 1)"]


def test_unsupported_language(db, model):
    repo_id = index_files(704, "rusty", {"README.md": "# Rusty\n\nA CLI tool.\n", "src/main.rs": "fn main() {}\n"})
    body = gen(repo_id, "overview").json()
    assert body["status"] == "generated" and body["facts"]["languages"] == {"markdown": 1, "rust": 1}
    assert any("Import analysis does not cover: rust (1 files)" in n for n in body["notes"])


def test_semantic_search_supplements_evidence(repo, model, monkeypatch):
    with database.session_scope() as s:
        ids = s.scalars(select(CodeChunk.id).where(CodeChunk.repository_id == repo)).all()
        store.save_embeddings(s, [(i, vec(1, 0.5)) for i in ids], config.EMBEDDING_MODEL)
    monkeypatch.setattr(embedding_service, "get_provider", lambda: QueryProvider())
    body = gen(repo, "modules").json()
    assert any(s["role"] == "search" and s["similarity"] is not None for s in body["sources"])
    assert len(body["sources"]) <= documentation.MAX_SOURCES
    assert not any("Semantic search was not used" in n for n in body["notes"])


def test_llm_failure_and_errors(repo, monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: FakeLLM(error=LLMError(502, "llm_provider_error", "Gemini failed.")))
    r = gen(repo, "overview")
    assert r.status_code == 502 and r.json()["code"] == "llm_provider_error"
    assert client.get(f"/api/repos/{repo}/docs").json()["sections"] == []  # failures are not cached
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    monkeypatch.undo()
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    assert gen(repo, "overview").json()["code"] == "llm_not_configured"
    assert gen(repo, "nope").status_code == 404 and gen(99999, "overview").status_code == 404


def test_document_and_markdown_export(repo, model):
    gen(repo, "api")
    gen(repo, "overview")
    doc = client.get(f"/api/repos/{repo}/docs").json()
    assert [s["section"] for s in doc["sections"]] == ["overview", "api"]  # documentation order
    md = doc["markdown"]
    assert md.startswith("# o/tasker documentation") and "AI-assisted" in md
    assert md.index("## Repository Overview") < md.index("## API Overview")
    assert "- [S1] `README.md` lines 1-" in md and "(cited)" in md
    catalog = client.get("/api/repos/docs/sections").json()
    assert [c["id"] for c in catalog] == ["overview", "architecture", "setup", "api", "modules", "structure",
                                          "developer_guide"]


def test_routes_in_docstrings_and_comments_are_ignored(db, model):
    lib = ('def stream(fn):\n    """Wrap a generator.\n\n    Example::\n\n        @app.get("/stream")\n'
           '        def view():\n            ...\n    """\n    return fn\n')
    go = '/*\nExample:\n\tr.GET("/docs", h)\n*/\npackage web\n\n// Example:\n//   r.GET("/ping", handler)\nfunc Setup(r *Router) {\n\tr.POST("/items", create)\n}\n'
    repo_id = index_files(705, "lib", {"lib/helpers.py": lib, "web/setup.go": go})
    routes = gen(repo_id, "api").json()["facts"]["routes"]
    assert routes == ["POST /items (gin) - web/setup.go:10"]


def test_manifests_are_cached_and_fetch_failures_noted(repo, model, clean, monkeypatch):
    gen(repo, "setup")
    first = len(clean["files"])
    gen(repo, "setup", regenerate=True)
    assert len(clean["files"]) == first and clean["tree"] == 1  # no extra GitHub requests on regenerate
    documentation._tree_cache.clear()
    documentation._tree_cache[(repo, documentation._repository(repo)["version"])] = [
        {"path": "requirements.txt", "type": "file"}]

    async def limited(*a):
        raise github.GitHubError(429, "rate_limited", "GitHub API rate limit exceeded")
    monkeypatch.setattr(github, "get_file_content", limited)
    body = gen(repo, "setup", regenerate=True).json()
    assert "Could not fetch from GitHub (rate limit or network): requirements.txt." in body["notes"]
    assert body["status"] == "generated"  # README installation part is still evidence
