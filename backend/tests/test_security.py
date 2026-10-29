"""Phase 12: secret masking, security rules + AI review, debug investigation (stub LLM; rules, DB and API are real)."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.db import database, store
from app.db.models import CodeChunk
from app.main import app
from app.services import embedding_service, llm, secrets, security
from tests.helpers import index_files
from tests.test_rag import FakeLLM
from tests.test_search import QueryProvider, vec

client = TestClient(app)

# Fake credentials, assembled at runtime so no realistic token is stored literally in the repository.
AWS_KEY = "AKIA" + "Q3EXAMPLEKEY7Z2B"
GH_TOKEN = "ghp_" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z5A"
DB_PASSWORD = "S3cr3t-Pa55w0rd"
PEM_BODY = "MIIEowIBAAKCAQEA7bq9xY2" + "\nz8Kq1wR5tV9uX3mN7pL2kJ6hG4fD8sA1"

CONFIG_PY = f'''import os

AWS_ACCESS_KEY_ID = "{AWS_KEY}"
DATABASE_URL = "postgresql://admin:{DB_PASSWORD}@db:5432/app"
API_TOKEN = os.environ["API_TOKEN"]
PASSWORD = "changeme"
SECRET_KEY = "your-secret-key-here"
PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
{PEM_BODY}
-----END RSA PRIVATE KEY-----"""
'''

VIEWS_PY = '''import pickle
import subprocess
from flask import request


def search(db, term):
    return db.execute(f"SELECT * FROM items WHERE name = '{term}'")


def convert(name):
    subprocess.run("convert " + name, shell=True)


def download():
    return open(request.args["path"]).read()


def restore(blob):
    return pickle.loads(blob)
'''

AUTH_PY = '''import jwt


def read_token(token):
    return jwt.decode(token, options={"verify_signature": False})


def is_admin(password):
    if password == "hunter2-admin":
        return True
    return False
'''

SAFE_PY = '''import os
import subprocess


def search(db, term):
    return db.execute("SELECT * FROM items WHERE name = ?", (term,))


def listing(path):
    # os.system("ls " + path) was replaced by a safe call
    return subprocess.run(["ls", path], check=True)


def guard(expr):
    raise ValueError("eval() is not allowed here")


SECRET = os.environ.get("APP_SECRET")


def render_template_string(source, **context):
    return source
'''

UI_JS = '''export function show(el, userInput) {
  el.innerHTML = userInput;
}

export function run(code) {
  console.log("never call eval() on user input");
  const fixed = eval('1 + 1');
  return eval(code);
}

export async function find(db, id) {
  return db.query(`SELECT * FROM users WHERE id = ${id}`);
}
'''

TEST_PY = '''def test_app(app):
    app.run(debug=True)
'''

LIB_RS = f'''pub fn token() -> &'static str {{
    let token = "{GH_TOKEN}";
    std::process::Command::new("sh").arg("-c").arg(token);
    token
}}
'''

FILES = {"app/config.py": CONFIG_PY, "app/views.py": VIEWS_PY, "app/auth.py": AUTH_PY, "app/safe.py": SAFE_PY,
         "web/ui.js": UI_JS, "tests/test_views.py": TEST_PY, "native/src/lib.rs": LIB_RS}
RAW_SECRETS = [AWS_KEY, GH_TOKEN, DB_PASSWORD, "7bq9xY2", "z8Kq1wR5tV9uX3mN7pL2kJ6hG4fD8sA1", "hunter2-admin"]


@pytest.fixture(autouse=True)
def fresh_cache():
    security._cache.clear()


@pytest.fixture
def repo(db):
    return index_files(601, "sec", FILES)


def scan(repo_id, **body):
    r = client.post(f"/api/repos/{repo_id}/security", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def at(body, path):
    return {(f["rule_id"], f["start_line"]) for f in body["findings"] if f["file_path"] == path}


def line_of(text, needle):
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


# ------------------------------------------------------------------ secrets


def test_masking_keeps_lengths_and_lines():
    masked, found = secrets.mask_secrets(CONFIG_PY)
    assert len(masked) == len(CONFIG_PY) and masked.count("\n") == CONFIG_PY.count("\n")
    assert {f["kind"] for f in found} == {"aws_access_key", "url_password", "private_key"}
    for raw in (AWS_KEY, DB_PASSWORD, "z8Kq1wR5tV9uX3mN7pL2kJ6hG4fD8sA1"):
        assert raw not in masked
    assert "AKIA************" in masked  # short prefix kept so the user can tell which key it was
    assert "-----BEGIN RSA PRIVATE KEY-----" in masked and "-----END RSA PRIVATE KEY-----" in masked
    # placeholders and environment lookups are not secrets
    for safe in ('PASSWORD = "changeme"', 'SECRET_KEY = "your-secret-key-here"', 'os.environ["API_TOKEN"]'):
        assert safe in masked
    assert secrets.mask('label = "credentialless"; password: "my-secret-password"') == \
        'label = "credentialless"; password: "my-secret-password"'
    assert secrets.mask("") == ""


def test_secret_findings_are_masked_everywhere(repo):
    body = scan(repo)
    found = {f["rule_id"] for f in body["findings"] if f["category"] == "secrets"}
    assert {"SECRET-AWS_ACCESS_KEY", "SECRET-URL_PASSWORD", "SECRET-PRIVATE_KEY", "SECRET-GITHUB_TOKEN"} <= found
    aws = next(f for f in body["findings"] if f["rule_id"] == "SECRET-AWS_ACCESS_KEY")
    assert (aws["file_path"], aws["start_line"], aws["severity"]) == ("app/config.py", 3, "high")
    assert aws["snippet"] == 'AWS_ACCESS_KEY_ID = "AKIA****************"' and aws["requires_review"] is True
    key = next(f for f in body["findings"] if f["rule_id"] == "SECRET-PRIVATE_KEY")
    assert (key["start_line"], key["end_line"]) == (9, 10)  # the masked key body
    dumped = json.dumps(body)
    assert not any(raw in dumped for raw in RAW_SECRETS[:5])


# ------------------------------------------------------------------ rules


def test_unsafe_input_injection_and_command_execution(repo):
    body = scan(repo)
    assert at(body, "app/views.py") == {
        ("PY-SQL-FORMAT", line_of(VIEWS_PY, "db.execute(f")),
        ("PY-CMD-SHELL", line_of(VIEWS_PY, "shell=True")),
        ("PY-PATH-INPUT", line_of(VIEWS_PY, "request.args")),
        ("PY-PICKLE", line_of(VIEWS_PY, "pickle.loads")),
    }
    assert at(body, "web/ui.js") == {("JS-XSS-SINK", 2), ("JS-EVAL", line_of(UI_JS, "eval(code)")),
                                     ("JS-SQL-TEMPLATE", line_of(UI_JS, "db.query"))}
    sql = next(f for f in body["findings"] if f["rule_id"] == "PY-SQL-FORMAT")
    assert sql["category"] == "injection" and sql["severity"] == "high" and "parameterized" in sql["recommendation"]
    assert sql["origin"] == "rule" and sql["ai_review"] is None and sql["non_production"] is False


def test_auth_findings(repo):
    body = scan(repo)
    assert at(body, "app/auth.py") == {("JWT-NO-VERIFY", line_of(AUTH_PY, "verify_signature")),
                                       ("HARDCODED-PASSWORD-CHECK", line_of(AUTH_PY, "hunter2"))}
    assert {f["category"] for f in body["findings"] if f["file_path"] == "app/auth.py"} == {"auth"}
    assert "hunter2-admin" not in json.dumps(body)  # the literal compared against is masked as a secret value


def test_safe_code_has_no_findings(repo):
    body = scan(repo)
    assert at(body, "app/safe.py") == set()  # parameterized SQL, list args, comment, string mention, env secret, def


def test_false_positive_controls(repo):
    body = scan(repo)
    js_lines = {line for rule, line in at(body, "web/ui.js")}
    assert line_of(UI_JS, "console.log") not in js_lines and line_of(UI_JS, "eval('1 + 1')") not in js_lines
    debug = next(f for f in body["findings"] if f["file_path"] == "tests/test_views.py")
    assert (debug["rule_id"], debug["non_production"], debug["severity"]) == ("PY-DEBUG", True, "low")  # medium lowered
    # non-production findings are listed after production ones of the same severity
    order = [f["id"] for f in body["findings"]]
    assert order == [f"F{i}" for i in range(1, len(order) + 1)]
    assert body["findings"][0]["severity"] == "high"


def test_unsupported_language_gets_secret_detection_only(repo):
    body = scan(repo)
    assert at(body, "native/src/lib.rs") == {("SECRET-GITHUB_TOKEN", 2)}  # no rule for Command::new("sh") in Rust
    assert body["secrets_only_languages"] == {"rust": 1}
    assert body["rule_languages"] == {"python": 5, "javascript": 1}
    assert any("Python, JavaScript/TypeScript and Go" in note for note in body["limitations"])


def test_path_prefix_cap_and_counts(repo, monkeypatch):
    body = scan(repo, path_prefix="app/")
    assert {f["file_path"] for f in body["findings"]} <= {"app/config.py", "app/views.py", "app/auth.py"}
    assert body["counts"]["by_category"]["secrets"] == 3
    monkeypatch.setattr(security, "MAX_FINDINGS", 3)
    capped = scan(repo)
    assert len(capped["findings"]) == 3 and capped["truncated"] is True and capped["total_findings"] > 3


def test_scan_cache_is_invalidated_by_reindexing(repo):
    first = scan(repo)["total_findings"]
    with database.session_scope() as s:
        s.execute(CodeChunk.__table__.delete().where(CodeChunk.repository_id == repo, CodeChunk.file_path == "app/views.py"))
        store.get_repository(s, repo).indexed_at = __import__("datetime").datetime.now()
    assert scan(repo)["total_findings"] == first - 4


def test_errors_and_validation(db, repo, monkeypatch):
    assert client.post("/api/repos/99999/security", json={}).status_code == 404
    assert client.post(f"/api/repos/{repo}/security", json={"path_prefix": "x" * 1001}).status_code == 422
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    r = client.post(f"/api/repos/{repo}/security", json={"use_ai": True})
    assert r.status_code == 503 and r.json()["code"] == "llm_not_configured"
    assert scan(repo)["ai"]["status"] == "not_requested"  # the deterministic scan needs no key


# ------------------------------------------------------------------ AI review (stub model)


def ai_json(**data):
    return FakeLLM(json.dumps(data))


def test_ai_review_is_validated(repo, monkeypatch):
    first = scan(repo)
    sql = first["findings"][0]  # the most severe production finding is always among the reviewed ones
    model = ai_json(
        reviews=[{"finding": sql["id"], "verdict": "likely_issue", "reasoning": "term comes from the caller."},
                 {"finding": "F999", "verdict": "likely_issue", "reasoning": "unknown"},
                 {"finding": sql["id"], "verdict": "definitely", "reasoning": "bad verdict"}],
        findings=[
            {"title": "Admin check uses a literal password", "category": "auth", "severity": "high", "confidence": "medium",
             "source": "S1", "start_line": 0, "end_line": 0, "explanation": "x", "recommendation": "y"},  # replaced below
            {"title": "Ghost", "category": "auth", "severity": "high", "source": "S99", "start_line": 1, "end_line": 1},
            {"title": "Bad label", "category": "telepathy", "severity": "high", "source": "S1", "start_line": 1, "end_line": 1},
            "not an object",
        ])
    monkeypatch.setattr(llm, "get_llm", lambda: model)
    # Point the valid proposal at real lines of the first source the model will see.
    sources = security._review_sources(repo, [f for f in first["findings"] if f["severity"] == "high"
                                              and not f["non_production"]][:security.AI_REVIEW_LIMIT], None)
    s1 = sources[0]
    data = json.loads(model.text)
    data["findings"][0].update(start_line=s1["start_line"], end_line=s1["start_line"])
    data["findings"][0]["explanation"] = f"Leaks {AWS_KEY} if logged."  # secrets in model output are masked too
    model.text = json.dumps(data)

    body = scan(repo, use_ai=True)
    ai = body["ai"]
    assert (ai["status"], ai["reviewed"], ai["new_findings"], ai["rejected"]) == ("ok", 1, 1, 3)
    reviewed = next(f for f in body["findings"] if f["rule_id"] == sql["rule_id"] and f["start_line"] == sql["start_line"])
    assert reviewed["ai_review"] == {"verdict": "likely_issue", "reasoning": "term comes from the caller."}
    new = next(f for f in body["findings"] if f["origin"] == "ai")
    assert new["source_id"] == "S1" and new["file_path"] == s1["file_path"] and new["requires_review"] is True
    assert new["rule_id"] is None and AWS_KEY not in new["explanation"]
    assert [s["source_id"] for s in ai["sources"]][:1] == ["S1"]
    system, prompt = model.calls[0]
    assert model.kwargs == {"json_output": True}
    assert not any(raw in prompt for raw in RAW_SECRETS) and "never claim certainty" in system.lower()
    assert not any(raw in json.dumps(body) for raw in RAW_SECRETS[:5])
    assert scan(repo)["findings"][0]["ai_review"] is None  # AI annotations never leak into the cached scan


def test_ai_unparseable_keeps_rule_findings(repo, monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: FakeLLM("I think it is fine."))
    body = scan(repo, use_ai=True)
    assert body["ai"]["status"] == "unparseable" and body["total_findings"] == scan(repo)["total_findings"]


def test_llm_generate_masks_prompts_and_answers():
    model = FakeLLM(f"The key {AWS_KEY} is exposed.")
    result = asyncio.run(llm.generate("system", f"code: TOKEN = '{GH_TOKEN}'", provider=model))
    assert GH_TOKEN not in model.calls[0][1] and "ghp_" in model.calls[0][1]
    assert AWS_KEY not in result.text and "AKIA****" in result.text


# ------------------------------------------------------------------ debug investigation

BUGGY_PY = '''import json


def add_item(item, bucket=[]):
    bucket.append(item)
    return bucket


def load(path):
    try:
        return json.load(open(path))
    except:
        return None


def save(path, data):
    try:
        json.dump(data, open(path, "w"))
    except OSError:
        pass
'''

JOBS_JS = '''export async function sendAll(users) {
  users.forEach(async (u) => {
    await send(u);
  });
  try { await flush(); } catch (e) {}
}
'''


@pytest.fixture
def debug_repo(db, monkeypatch):
    repo_id = index_files(602, "dbg", {"app/cart.py": BUGGY_PY, "web/jobs.js": JOBS_JS, "app/config.py": CONFIG_PY})
    with database.session_scope() as s:
        ids = s.scalars(select(CodeChunk.id).where(CodeChunk.repository_id == repo_id)).all()
        store.save_embeddings(s, [(i, vec(1, 0.5)) for i in ids], config.EMBEDDING_MODEL)
    monkeypatch.setattr(embedding_service, "get_provider", lambda: QueryProvider())
    return repo_id


def investigate(repo_id, **body):
    return client.post(f"/api/repos/{repo_id}/debug", json={"description": "Items from earlier requests show up in the cart", **body})


def test_debug_rule_hints_without_ai(debug_repo, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)  # no key needed without AI
    r = investigate(debug_repo, use_ai=False)
    assert r.status_code == 200, r.text
    body = r.json()
    hints = {(f["rule_id"], f["file_path"], f["start_line"]) for f in body["findings"]}
    assert {("PY-MUTABLE-DEFAULT", "app/cart.py", 4), ("PY-BARE-EXCEPT", "app/cart.py", 12),
            ("PY-SWALLOWED", "app/cart.py", 19), ("JS-ASYNC-FOREACH", "web/jobs.js", 2),
            ("JS-EMPTY-CATCH", "web/jobs.js", 5)} <= hints
    assert body["ai"]["status"] == "not_requested" and body["summary"] is None
    assert all(f["requires_review"] and f["source_id"] for f in body["findings"])
    assert not any(raw in json.dumps(body) for raw in RAW_SECRETS[:5])  # config.py was retrieved, masked


def test_debug_ai_findings_and_citations(debug_repo, monkeypatch):
    model = FakeLLM()
    monkeypatch.setattr(llm, "get_llm", lambda: model)
    first = investigate(debug_repo, use_ai=False).json()
    cart = next(s for s in first["sources"] if s["symbol_name"] == "add_item")
    model.text = json.dumps({
        "summary": f"The default list in `add_item` is shared between calls [{cart['source_id']}]. Also [S42].",
        "findings": [
            {"title": "Shared mutable default", "category": "logic", "severity": "high", "confidence": "high",
             "source": cart["source_id"], "start_line": 4, "end_line": 6, "explanation": "bucket persists.",
             "reasoning": "Python evaluates defaults once.", "suggestion": "Use bucket=None."},
            {"title": "Out of range", "category": "logic", "severity": "high", "source": cart["source_id"],
             "start_line": 400, "end_line": 401},
            {"title": "No source", "category": "logic", "severity": "low", "source": "S42", "start_line": 1, "end_line": 1},
        ]})
    body = investigate(debug_repo).json()
    assert body["ai"] == {"status": "ok", "new_findings": 1, "rejected": 2}
    assert "[S42]" not in body["summary"] and body["citations"]["invalid"] == ["S42"]
    assert cart["source_id"] in body["citations"]["cited"]
    ai = next(f for f in body["findings"] if f["origin"] == "ai")
    assert (ai["file_path"], ai["start_line"], ai["end_line"], ai["severity"]) == ("app/cart.py", 4, 6, "high")
    assert ai["snippet"].startswith("def add_item(item, bucket=[]):") and ai["reasoning"] and ai["recommendation"] == "Use bucket=None."
    assert body["findings"][0]["id"] == "F1" and body["findings"][0]["origin"] == "ai"  # most severe first
    system, prompt = model.calls[0]
    assert "PY-MUTABLE-DEFAULT" not in prompt and "Mutable default argument" in prompt  # hints listed by title
    assert "Items from earlier requests" in prompt and model.kwargs == {"json_output": True}
    assert not any(raw in prompt for raw in RAW_SECRETS)


def test_debug_pinned_file_without_embeddings(db, monkeypatch):
    repo_id = index_files(603, "noemb", {"app/cart.py": BUGGY_PY})
    monkeypatch.setattr(embedding_service, "get_provider", lambda: QueryProvider())
    r = investigate(repo_id, use_ai=False, file_path="app/cart.py", start_line=9, end_line=13)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["partial"] is True and "only the pinned file" in body["message"]
    assert [(s["source_id"], s["start_line"], s["end_line"]) for s in body["sources"]] == [("S1", 9, 13)]
    assert {f["rule_id"] for f in body["findings"]} == {"PY-BARE-EXCEPT"}
    assert investigate(repo_id, use_ai=False).json()["code"] == "embeddings_not_ready"


def test_debug_validation(debug_repo, monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: FakeLLM())
    assert investigate(debug_repo, description="   ").json()["code"] == "empty_query"
    assert investigate(debug_repo, top_k=13).json()["code"] == "invalid_top_k"
    assert investigate(debug_repo, description="x" * 1001).status_code == 400
    assert investigate(debug_repo, file_path="nope.py").status_code == 404
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    monkeypatch.undo()
    monkeypatch.setattr(embedding_service, "get_provider", lambda: QueryProvider())
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    assert investigate(debug_repo).json()["code"] == "llm_not_configured"


# ------------------------------------------------------------------ masking at every display boundary


def test_display_endpoints_mask_secrets_without_touching_the_database(repo, monkeypatch):
    page = client.get(f"/api/repos/{repo}/chunks", params={"file_path": "app/config.py"}).json()
    shown = "\n".join(c["content"] for c in page["chunks"])
    assert AWS_KEY not in shown and DB_PASSWORD not in shown and "AKIA****" in shown
    with database.session_scope() as s:  # stored chunks are unchanged (masking works on copies)
        stored = "\n".join(s.scalars(select(CodeChunk.content).where(CodeChunk.repository_id == repo)).all())
    assert AWS_KEY in stored and DB_PASSWORD in stored

    with database.session_scope() as s:
        ids = s.scalars(select(CodeChunk.id).where(CodeChunk.repository_id == repo)).all()
        store.save_embeddings(s, [(i, vec(1, 0.5)) for i in ids], config.EMBEDDING_MODEL)
    monkeypatch.setattr(embedding_service, "get_provider", lambda: QueryProvider())
    results = client.post(f"/api/repos/{repo}/search", json={"query": "database url", "top_k": 20}).json()
    assert results["results"] and not any(raw in json.dumps(results) for raw in RAW_SECRETS[:5])

    impact = client.post(f"/api/repos/{repo}/impact", json={"file_path": "app/config.py"}).json()
    assert not any(raw in json.dumps(impact) for raw in RAW_SECRETS)


def test_file_viewer_masks_secrets(monkeypatch):
    from app.api import repos as api

    async def fake_file(owner, repo, branch, path):
        return {"path": path, "name": "config.py", "language": "python", "size": len(CONFIG_PY), "kind": "text",
                "content": CONFIG_PY, "message": None, "html_url": None}

    monkeypatch.setattr(api, "get_file_content", fake_file)
    body = client.get("/api/repos/file", params={"owner": "o", "repo": "r", "branch": "main", "path": "app/config.py"}).json()
    assert body["secrets_masked"] == 3 and AWS_KEY not in body["content"]
    assert body["content"].count("\n") == CONFIG_PY.count("\n") and len(body["content"]) == len(CONFIG_PY)


def test_debug_selected_function_without_description(debug_repo, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    r = investigate(debug_repo, description="", use_ai=False, file_path="app/cart.py", symbol="load")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] == "Potential bugs in function load (app/cart.py)"
    s1 = body["sources"][0]
    assert (s1["source_id"], s1["role"], s1["start_line"], s1["end_line"]) == ("S1", "focus", 9, 13)
    assert ("PY-BARE-EXCEPT", 12) in {(f["rule_id"], f["start_line"]) for f in body["findings"]}
    assert investigate(debug_repo, description="", use_ai=False).json()["code"] == "empty_query"  # nothing selected
    assert investigate(debug_repo, use_ai=False, symbol="load").json()["code"] == "invalid_selection"
    assert investigate(debug_repo, use_ai=False, file_path="app/cart.py", symbol="nope").json()["code"] == "symbol_not_found"
