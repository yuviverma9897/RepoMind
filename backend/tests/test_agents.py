"""Phase 14: orchestrator routing and agents (stub LLM/embeddings; the services, DB and API are real)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.agents import orchestrator, specialists
from app.db import database, store
from app.db.models import CodeChunk
from app.main import app
from app.services import dependencies, documentation, embedding_service, github, llm, security
from app.services.llm import LLMError
from tests.helpers import index_files
from tests.test_rag import FakeLLM
from tests.test_search import QueryProvider, vec

client = TestClient(app)
AUTH = '''import os
from .db import get_db

API_KEY = "sk_live_''' + "51HxAbCdEfGhIjKlMnOpQrStUv" + '''"


def login(username, password):
    db = get_db()
    user = db.find(username)
    try:
        return user.check(password)
    except:
        return False


def run_report(name):
    os.system("report " + name)
'''
DB = '''def get_db():
    """Return the shared connection."""
    return connect()


def connect():
    return object()
'''
APP = '''from .auth import login
from .db import get_db


def create_app():
    return {"login": login, "db": get_db()}
'''
FILES = {"README.md": "# Shop\n\nA small shop backend.\n\n## Installation\n\npip install shop\n",
         "shop/auth.py": AUTH, "shop/db.py": DB, "shop/app.py": APP, "tests/test_auth.py": "def test_login():\n    assert True\n"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    documentation._cache.clear()
    documentation._tree_cache.clear()
    dependencies._cache.clear()
    security._cache.clear()

    async def no_github(*a):
        raise github.GitHubError(503, "github_unavailable", "GitHub not reachable in tests")
    monkeypatch.setattr(github, "import_repository", no_github)
    monkeypatch.setattr(github, "get_file_content", no_github)


@pytest.fixture
def repo(db):
    return index_files(801, "shop", FILES)


@pytest.fixture
def model(monkeypatch):
    stub = FakeLLM("`get_db` returns the shared connection [S1].")
    monkeypatch.setattr(llm, "get_llm", lambda: stub)
    return stub


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")


def embed(repo_id, monkeypatch):
    with database.session_scope() as s:
        ids = s.scalars(select(CodeChunk.id).where(CodeChunk.repository_id == repo_id)).all()
        store.save_embeddings(s, [(i, vec(1, 0.5)) for i in ids], config.EMBEDDING_MODEL)
    monkeypatch.setattr(embedding_service, "get_provider", lambda: QueryProvider())


def ask(repo_id, query):
    r = client.post(f"/api/repos/{repo_id}/agent", json={"query": query})
    assert r.status_code == 200, r.text
    return r.json()


def steps(body):
    return [(s["agent"], s["status"]) for s in body["activity"]]


# ---------------------------------------------------------------- routing (pure function)

FILE_LIST = ["shop/auth.py", "shop/db.py", "shop/app.py", "tests/test_auth.py", "README.md"]


@pytest.mark.parametrize("query,agent,extra", [
    ("How many files and which languages does it use?", "repository", {}),
    ("What does this project do?", "research", {"fallback": True}),
    ("How does error handling work?", "research", {}),
    ("Explain the get_db function", "code_intelligence", {"symbol": "get_db"}),
    ("what does shop/auth.py do?", "code_intelligence", {"file_path": "shop/auth.py"}),
    ("auth.py", "code_intelligence", {"file_path": "shop/auth.py"}),
    ("Are there any security vulnerabilities in shop/?", "security", {"file_path": "shop/"}),
    ("Find hardcoded API keys", "security", {}),
    ("What breaks if I change db.py?", "impact", {"file_path": "shop/db.py"}),
    ("How do I install and run this?", "documentation", {"section": "setup"}),
    ("Generate API documentation", "documentation", {"section": "api"}),
    ("Write documentation for this repo", "documentation", {"section": "overview"}),
    ("Why does login fail with a KeyError?", "debug", {}),
    ("security bug in auth.py", "security", {"ambiguous": True, "alternatives": ["debug"]}),
])
def test_routing(query, agent, extra):
    decision = orchestrator.route(query, FILE_LIST)
    assert decision["agent"] == agent, decision
    for key, value in extra.items():
        assert decision[key] == value, (key, decision)


@pytest.mark.parametrize("query", ["Push this to main", "deploy it to production", "write a new test for db.py",
                                   "run the tests", "open a pull request with the fix", "edit the code to add caching"])
def test_unsupported_requests_are_recognised(query):
    decision = orchestrator.route(query, FILE_LIST)
    assert decision["agent"] is None and decision["matched"]


def test_folder_scope_only_for_security():
    assert orchestrator.route("explain shop/", FILE_LIST)["file_path"] is None
    assert orchestrator.route("what is the commit history", FILE_LIST)["agent"] == "research"


# ---------------------------------------------------------------- agents through the API

def test_repository_question_is_deterministic(repo, monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: pytest.fail("the repository agent must not call the model"))
    body = ask(repo, "How many files and which languages does it use?")
    assert (body["agent"], body["status"], body["agent_title"]) == ("repository", "completed", "Repository Agent")
    facts = body["metadata"]["facts"]
    assert facts["files"] == 5 and facts["languages"] == {"python": 4, "markdown": 1} and facts["chunks"] > 0
    assert "Indexed files: 5" in body["answer"] and body["sources"] == []
    assert steps(body) == [("orchestrator", "completed"), ("repository", "completed")]
    assert all(s["duration_ms"] is not None and s["completed_at"] for s in body["activity"])


def test_code_explanation_locates_the_symbol(repo, model):
    body = ask(repo, "Explain the get_db function")
    assert (body["agent"], body["status"]) == ("code_intelligence", "completed")
    assert body["metadata"]["target"]["file_path"] == "shop/db.py" and body["metadata"]["target"]["label"].endswith("get_db")
    assert "[S1]" in body["answer"] and body["sources"][0]["file_path"] == "shop/db.py"
    assert "symbol definition: shop/db.py · get_db" in body["activity"][1]["result"]
    assert steps(body) == [("orchestrator", "completed"), ("code_intelligence", "completed"),
                           ("code_intelligence", "completed")]
    assert len(model.calls) == 1


def test_unlocatable_code_falls_back_to_research(repo, model, monkeypatch):
    embed(repo, monkeypatch)
    body = ask(repo, "Explain the frobnicate_widgets function")
    assert body["agent"] == "research" and body["routing"]["fallback"] is True
    assert "fell back to research" in body["routing"]["reason"]
    assert [s[0] for s in steps(body)] == ["orchestrator", "code_intelligence", "research"]
    assert "no definition of 'frobnicate_widgets'" in body["activity"][1]["result"]
    located = ask(repo, "Explain how the shared connection is created")  # no symbol named: semantic search locates
    assert located["agent"] == "code_intelligence" and "semantic search:" in located["activity"][1]["result"]


def test_documentation_request(repo, model):
    body = ask(repo, "How do I install it?")
    assert (body["agent"], body["metadata"]["section"], body["metadata"]["title"]) == ("documentation", "setup", "Setup Guide")
    assert body["status"] == "completed" and body["sources"][0]["file_path"] == "README.md"
    assert any("Could not read the file list from GitHub" in n for n in body["metadata"]["notes"])


def test_security_without_model_is_pattern_only(repo, no_model):
    body = ask(repo, "Are there security vulnerabilities?")
    assert (body["agent"], body["status"]) == ("security", "partial")
    assert steps(body) == [("orchestrator", "completed"), ("security", "skipped"), ("security", "completed")]
    rules = {f["rule_id"] for f in body["metadata"]["findings"]}
    assert "SECRET-STRIPE" in rules or any(r.startswith("SECRET") for r in rules)
    assert "51HxAbCdEfGhIjKlMnOpQrStUv" not in str(body)  # masked everywhere
    assert "not proof of a vulnerability" in body["answer"]


def test_security_with_model_runs_ai_review(repo, model):
    body = ask(repo, "security review of shop/")
    assert body["status"] == "completed" and body["metadata"]["ai"]["status"] in ("ok", "unparseable")
    assert body["routing"]["file_path"] == "shop/" and len(model.calls) == 1


def test_impact(repo, no_model):
    body = ask(repo, "What breaks if I change db.py?")
    assert (body["agent"], body["status"]) == ("impact", "partial")
    assert [d["file_path"] for d in body["metadata"]["impact"]["direct_dependents"]] == ["shop/app.py", "shop/auth.py"]
    assert "Direct dependents: 2" in body["answer"]
    needs = ask(repo, "what is the impact of a change?")
    assert (needs["agent"], needs["status"]) == ("impact", "needs_input")


def test_impact_with_model(repo, model):
    body = ask(repo, "What breaks if I change the get_db function in db.py?")
    assert body["status"] == "completed" and body["metadata"]["impact"]["symbol"] == "get_db"
    assert len(model.calls) == 1 and body["sources"]


def test_debug_without_model(repo, no_model):
    body = ask(repo, "Why does login in auth.py fail silently?")
    assert (body["agent"], body["status"]) == ("debug", "partial")
    assert ("debug", "skipped") in steps(body)
    assert any(f["rule_id"] == "PY-BARE-EXCEPT" for f in body["metadata"]["findings"])


def test_ambiguous_request_reports_alternatives(repo, no_model):
    body = ask(repo, "security bug in auth.py")
    assert body["agent"] == "security" and body["routing"]["ambiguous"] is True
    assert "chose the more specific intent" in body["routing"]["reason"]


def test_unsupported_request(repo, monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: pytest.fail("unsupported requests must not call the model"))
    body = ask(repo, "Push the fix to main and deploy it")
    assert (body["agent"], body["status"], body["routing"]["agent"]) == ("orchestrator", "unsupported", None)
    assert "never edits, runs, commits or deploys code" in body["answer"]
    assert steps(body) == [("orchestrator", "completed")]


def test_agent_failure_is_reported(repo, monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: FakeLLM(error=LLMError(429, "llm_rate_limited", "Gemini rate limit reached.",
                                                                        retry_after=12)))
    body = ask(repo, "Explain the get_db function")
    assert (body["agent"], body["status"]) == ("code_intelligence", "failed")
    assert body["error"]["code"] == "llm_rate_limited" and body["error"]["retry_after_seconds"] == 12
    assert body["activity"][-1]["status"] == "failed" and "rate limit" in body["activity"][-1]["result"]


def test_unexpected_agent_error_is_contained(repo, monkeypatch):
    async def boom(self, ctx, activity):
        async with activity.step(self.name, "Read repository metadata and index statistics"):
            raise RuntimeError("bug")
    monkeypatch.setattr(specialists.RepositoryAgent, "run", boom)
    body = ask(repo, "how many files?")
    assert (body["status"], body["error"]["code"]) == ("failed", "agent_error")
    assert "bug" not in str(body) and body["activity"][-1]["result"] == "Internal error (RuntimeError)"


def test_research_without_embeddings_fails_clearly(repo, model):
    body = ask(repo, "What does this project do?")
    assert (body["agent"], body["status"]) == ("research", "failed")
    assert body["error"]["code"] == "embeddings_not_ready"


def test_errors_and_catalog(repo):
    assert client.post("/api/repos/99999/agent", json={"query": "hi"}).status_code == 404
    assert client.post(f"/api/repos/{repo}/agent", json={"query": ""}).status_code == 422
    catalog = client.get("/api/agents").json()
    assert {a["name"]: a["uses_llm"] for a in catalog} == {
        "repository": "never", "research": "always", "code_intelligence": "always", "debug": "optional",
        "security": "optional", "documentation": "always", "impact": "optional"}
