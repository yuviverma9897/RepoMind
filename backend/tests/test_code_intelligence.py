"""Phase 10: explain files, functions, classes and selections (stub LLM; parsing, retrieval and API are real)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.db import database, store
from app.db.models import CodeChunk
from app.main import app
from app.services import code_intelligence, embedding_service, llm, repo_code
from tests.helpers import index_files
from tests.test_rag import FakeLLM
from tests.test_search import QueryProvider, vec

client = TestClient(app)

AUTH_PY = '''"""Authentication helpers."""
import hashlib
from .db import get_db


def check_password(stored, given):
    return stored == hashlib.sha256(given.encode()).hexdigest()


def login(username: str, password: str = "", *, remember=False) -> dict:
    """Log a user in and return the session payload."""
    user = get_db().execute("SELECT * FROM user WHERE username = ?", (username,)).fetchone()
    if user is None or not check_password(user["password"], password):
        raise ValueError("Incorrect username or password.")
    return {"user_id": user["id"], "remember": remember}


class Session(dict):
    """A signed-cookie session."""
    lifetime = 3600

    def save(self, response):
        response.set_cookie("session", self.serialize())

    def serialize(self):
        return repr(dict(self))
'''

API_TS = '''import { request } from "./http";

export async function fetchUser(id: string, { retries = 3 } = {}): Promise<User> {
  const res = await request(userUrl(id), { retries });
  return parseUser(res);
}

export class UserApi extends BaseApi {
  constructor(private base: string) { super(); }

  async get(path: string): Promise<User> {
    return fetchUser(this.base + path);
  }
}
'''

SERVER_GO = '''package server

import "net/http"

func (s *Server) Handle(w http.ResponseWriter, r *http.Request) error {
	user := s.lookup(r.URL.Query().Get("id"))
	return writeJSON(w, user)
}
'''

DEPLOY_RB = '''class Deployer
  def run(env)
    system("deploy #{env}")
  end
end
'''


@pytest.fixture
def repo(db):
    return index_files(501, "code", {"app/auth.py": AUTH_PY, "web/api.ts": API_TS, "srv/server.go": SERVER_GO,
                                     "ops/deploy.rb": DEPLOY_RB, "app/db.py": "def get_db():\n    return None\n"})


@pytest.fixture
def model(monkeypatch):
    stub = FakeLLM("### Purpose\nChecks credentials [S1] using `check_password` [S3]. Unknown [S9].")
    monkeypatch.setattr(llm, "get_llm", lambda: stub)
    return stub


def explain(repo_id, **body):
    return client.post(f"/api/repos/{repo_id}/explain", json=body)


def test_files_are_rebuilt_exactly_from_chunks(db):
    text = "\n".join(f"line {i} " + "x" * (i % 50) for i in range(1, 400)) + "\n"
    repo_id = index_files(502, "big", {"notes/big.txt": text, "app/auth.py": AUTH_PY}, chunk_size=300, overlap=60)
    assert repo_code.load_file(repo_id, "notes/big.txt")["lines"] == text.splitlines()
    assert repo_code.load_file(repo_id, "app/auth.py")["lines"] == AUTH_PY.splitlines()


def test_symbols_endpoint(repo):
    body = client.get(f"/api/repos/{repo}/symbols", params={"file_path": "app/auth.py"}).json()
    assert body["structure_support"] == "python-ast" and body["line_count"] == AUTH_PY.count("\n")
    assert [(s["kind"], s["name"], s["parent"]) for s in body["symbols"]] == [
        ("function", "check_password", None), ("function", "login", None), ("class", "Session", None),
        ("method", "save", "Session"), ("method", "serialize", "Session")]  # methods of a single-chunk class too
    session = body["symbols"][2]
    assert AUTH_PY.splitlines()[session["start_line"] - 1] == "class Session(dict):"
    assert session["end_line"] == AUTH_PY.count("\n")  # container spans its methods
    assert client.get(f"/api/repos/{repo}/symbols", params={"file_path": "ops/deploy.rb"}).json()["structure_support"] is None


def test_python_function(repo, model):
    r = explain(repo, file_path="app/auth.py", symbol="login")
    assert r.status_code == 200, r.text
    body = r.json()
    s = body["structure"]
    assert (s["name"], s["returns"], s["is_async"], s["docstring"]) == ("login", "dict", False, "Log a user in and return the session payload.")
    assert s["parameters"] == [
        {"name": "username", "type": "str", "default": None, "kind": "positional"},
        {"name": "password", "type": "str", "default": "''", "kind": "positional"},
        {"name": "remember", "type": None, "default": "False", "kind": "keyword-only"}]
    assert {"get_db", "check_password", "ValueError"} <= set(s["calls"]) and s["raises"] == ["ValueError"]
    target = body["target"]
    assert (target["kind"], target["label"]) == ("function", "login")
    assert AUTH_PY.splitlines()[target["start_line"] - 1].startswith("def login(")
    roles = [(src["source_id"], src["role"], src["symbol_name"] or src["chunk_type"]) for src in body["sources"]]
    assert roles[:2] == [("S1", "target", "login"), ("S2", "imports", "imports")]
    assert ("S3", "definition", "check_password") in roles and any(r[2] == "get_db" for r in roles)  # other file too
    # citations validated like /ask
    assert body["citations"] == {"cited": ["S1", "S3"], "invalid": ["S9"]} and "[S9]" not in body["answer"]
    system, prompt = model.calls[0]
    assert "### Inputs" in system and "### Edge cases and risks" in system and "o/code" in system
    assert '"name": "remember"' in prompt and "def login(" in prompt


def test_python_method_and_class(repo, model):
    method = explain(repo, file_path="app/auth.py", symbol="save", parent="Session").json()
    assert method["target"]["label"] == "Session.save" and method["structure"]["parameters"][0]["name"] == "self"
    assert "response.set_cookie" in method["structure"]["calls"]

    cls = explain(repo, file_path="app/auth.py", symbol="Session").json()
    assert cls["target"]["kind"] == "class" and "### Methods" in model.calls[-1][0]
    assert cls["structure"]["methods"] == ["save", "serialize"] and cls["structure"]["bases"] == ["dict"]
    assert cls["structure"]["attributes"] == ["lifetime"]


def test_typescript_function_and_class(repo, model):
    fn = explain(repo, file_path="web/api.ts", symbol="fetchUser").json()["structure"]
    assert (fn["name"], fn["is_async"], fn["method"]) == ("fetchUser", True, "heuristic")
    assert [p["name"] for p in fn["parameters"]] == ["id: string", "{ retries = 3 } = {}"]
    assert fn["returns"] == "Promise<User>" and fn["calls"][:3] == ["request", "userUrl", "parseUser"]

    cls = explain(repo, file_path="web/api.ts", symbol="UserApi").json()
    assert cls["structure"]["name"] == "UserApi" and "extends BaseApi" in cls["structure"]["extends"]
    assert "get" in cls["structure"]["members"]


def test_go_method_with_receiver(repo, model):
    s = explain(repo, file_path="srv/server.go", symbol="Handle").json()["structure"]
    assert (s["name"], s["receiver"], s["returns"]) == ("Handle", "s *Server", "error")
    assert [p["name"] for p in s["parameters"]] == ["w http.ResponseWriter", "r *http.Request"]
    assert "writeJSON" in s["calls"]


def test_whole_file(repo, model):
    body = explain(repo, file_path="app/auth.py").json()
    assert body["target"]["kind"] == "file" and body["structure"]["kind"] == "module"
    assert body["structure"]["functions"] == ["check_password", "login"] and body["structure"]["imports"] == [".db", "hashlib"]
    assert [s["role"] for s in body["sources"]][0] == "target"


def test_unsupported_language_still_explained_without_structure(repo, model):
    body = explain(repo, file_path="ops/deploy.rb", start_line=2, end_line=4).json()
    assert body["structure"] is None and "No structural analysis for ruby" in body["structure_note"]
    assert body["target"]["kind"] == "selection" and len(model.calls) == 1


def test_partial_python_selection(repo, model):
    body = explain(repo, file_path="app/auth.py", start_line=13, end_line=13).json()  # `if ...:` without its body
    assert body["structure"] is None and "not complete Python" in body["structure_note"]


@pytest.mark.parametrize("payload, status, code", [
    ({"start_line": 5, "end_line": 2}, 400, "invalid_selection"),
    ({"start_line": 0, "end_line": 2}, 400, "invalid_selection"),
    ({"start_line": 3}, 400, "invalid_selection"),
    ({"start_line": 1, "end_line": 999}, 400, "invalid_selection"),
    ({"symbol": "nope"}, 404, "symbol_not_found"),
])
def test_malformed_requests(repo, model, payload, status, code):
    r = explain(repo, file_path="app/auth.py", **payload)
    assert (r.status_code, r.json()["code"]) == (status, code)
    assert model.calls == []


def test_selection_size_limit_and_unknown_file(db, model):
    repo_id = index_files(503, "long", {"long.py": "x = 1\n" * 900})
    r = explain(repo_id, file_path="long.py", start_line=1, end_line=401)
    assert r.json()["code"] == "selection_too_large"
    assert explain(repo_id, file_path="missing.py").json()["code"] == "file_not_indexed"
    assert explain(9999, file_path="a.py").status_code == 404


def test_large_file_is_truncated(db, model):
    repo_id = index_files(504, "huge", {"huge.py": "".join(f"VALUE_{i} = {i}\n" for i in range(3000))})
    body = explain(repo_id, file_path="huge.py").json()
    assert body["target"]["truncated"] and "were sent to the model" in body["target"]["note"]
    assert len(model.calls[0][1]) < code_intelligence.MAX_TARGET_CHARS + 6000


def test_missing_key(repo, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    r = explain(repo, file_path="app/auth.py", symbol="login")
    assert (r.status_code, r.json()["code"]) == (503, "llm_not_configured")


def test_ask_about_this_code_pins_the_focus(repo, model, monkeypatch):
    with database.session_scope() as s:  # embed everything far from the query -> weak search results
        ids = list(s.scalars(select(CodeChunk.id).where(CodeChunk.repository_id == repo)))
        store.save_embeddings(s, [(i, vec(0, 0, 1)) for i in ids], config.EMBEDDING_MODEL)
    monkeypatch.setattr(embedding_service, "get_provider", lambda: QueryProvider())
    model.text = "It raises ValueError on bad credentials [S1]."
    r = client.post(f"/api/repos/{repo}/ask", json={"question": "Why does this raise?", "top_k": 5,
                                                     "focus": {"file_path": "app/auth.py", "start_line": 11, "end_line": 16}})
    body = r.json()
    first = body["sources"][0]
    assert (first["role"], first["start_line"], first["end_line"], first["similarity"]) == ("focus", 11, 16, None)
    others = body["sources"][1:]
    assert others and not any(o["file_path"] == "app/auth.py" and o["start_line"] <= 16 and o["end_line"] >= 11 for o in others)
    assert body["evidence"]["level"] == "supported"  # pinned code counts as relevant evidence
    assert "code the user is looking at" in model.calls[0][1]
    bad = client.post(f"/api/repos/{repo}/ask", json={"question": "x?", "focus": {"file_path": "app/auth.py", "start_line": 9, "end_line": 2}})
    assert bad.json()["code"] == "invalid_selection"
