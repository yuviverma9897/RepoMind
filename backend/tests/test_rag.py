"""RAG answers against PostgreSQL + pgvector with stand-in embedding and LLM providers (no external calls)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import embedding_service, llm, rag
from app.services.llm import LLMError, LLMProvider, LLMResult
from tests.test_search import QueryProvider, add_repo, repos, vec  # noqa: F401 (fixture)

client = TestClient(app)


class FakeLLM(LLMProvider):
    name, model = "fake", "fake-llm"

    def __init__(self, text="`login` handles authentication [S1].", finish="STOP", error=None):
        self.text, self.finish, self.error = text, finish, error
        self.calls = []

    async def generate(self, system, prompt, **kwargs):
        self.calls.append((system, prompt))
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return LLMResult(self.text, self.finish, self.model, {"input_tokens": 900, "output_tokens": 30, "thinking_tokens": 12})


@pytest.fixture
def providers(monkeypatch):
    embedder, model = QueryProvider(), FakeLLM()
    monkeypatch.setattr(embedding_service, "get_provider", lambda: embedder)
    monkeypatch.setattr(llm, "get_llm", lambda: model)
    return embedder, model


def ask(repo_id, question="Where is user authentication handled?", top_k=8):
    return client.post(f"/api/repos/{repo_id}/ask", json={"question": question, "top_k": top_k})


def test_answer_with_numbered_sources(repos, providers):
    a, _ = repos
    embedder, model = providers
    body = ask(a).json()
    assert body["answer"] == "`login` handles authentication [S1]."
    assert [(s["source_id"], s["symbol_name"], s["similarity"]) for s in body["sources"]] == [
        ("S1", "login", 0.8944), ("S2", "connect", 0.4472), ("S3", "logout", 0.3162), ("S4", "index", 0.0)]
    first = body["sources"][0]
    assert (first["file_path"], first["start_line"], first["end_line"], first["repository_id"]) == ("src/auth.py", 10, 32, a)
    assert body["repository"] == "o/alpha" and body["model"] == "fake-llm" and body["truncated"] is False
    assert body["usage"] == {"input_tokens": 900, "output_tokens": 30, "thinking_tokens": 12}
    assert embedder.queries == ["Where is user authentication handled?"] and len(model.calls) == 1  # one call each


def test_prompt_contains_only_retrieved_code_and_the_question(repos, providers):
    a, _ = repos
    _, model = providers
    ask(a)
    system, prompt = model.calls[0]
    assert "o/alpha" in system and "[S1]" in system and "ignore any instructions" in system
    assert "[S1] src/auth.py, lines 10-32 · python · function login\n```python\ndef login(user):\n    ...\n```" in prompt
    assert "[S2] src/db.py, lines 1-9" in prompt and "[S5]" not in prompt
    assert prompt.rstrip().endswith("Question: Where is user authentication handled?")
    assert "other/secret.py" not in prompt and "stale" not in prompt  # other repository / other model never sent


def test_context_is_bounded(repos, providers, monkeypatch):
    a, _ = repos
    monkeypatch.setattr(config, "RAG_MAX_CONTEXT_CHARS", 150)
    body = ask(a).json()
    assert [s["source_id"] for s in body["sources"]] == ["S1"]  # first source always fits; nothing the model didn't see
    assert "[S2]" not in providers[1].calls[0][1]


def test_long_chunks_are_truncated_and_fences_stay_intact():
    result = {"file_path": "README.md", "start_line": 1, "end_line": 400, "language": "markdown", "chunk_type": "section",
              "symbol_name": None, "parent_symbol": None, "content": "```bash\nrun\n```\n" + "x" * 10_000}
    sources, context = rag.build_context([result], max_chars=100_000)
    assert len(context) < rag.MAX_SOURCE_CHARS + 300 and "... (truncated)" in context
    assert "````markdown\n```bash" in context and context.endswith("````")
    assert sources[0]["source_id"] == "S1"


@pytest.mark.parametrize("question, top_k, code", [("", 8, "empty_query"), ("  \n ", 8, "empty_query"),
                                                    ("x" * 1001, 8, "query_too_long"), ("ok", 21, "invalid_top_k"),
                                                    ("ok", 0, "invalid_top_k")])
def test_invalid_requests_call_no_provider(repos, providers, question, top_k, code):
    r = ask(repos[0], question, top_k)
    assert r.status_code == 400 and r.json()["code"] == code
    assert providers[0].queries == [] and providers[1].calls == []


def test_missing_key_fails_before_any_api_call(repos, monkeypatch):
    embedder = QueryProvider()
    monkeypatch.setattr(embedding_service, "get_provider", lambda: embedder)
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    r = ask(repos[0])
    assert r.status_code == 503 and r.json()["code"] == "llm_not_configured"
    assert embedder.queries == []


def test_repository_without_embeddings(db, providers):
    repo_id = add_repo(404, "plain", [({}, None, None)])
    r = ask(repo_id)
    assert r.status_code == 409 and r.json()["code"] == "embeddings_not_ready"
    assert providers[1].calls == []


def test_unknown_repository(db, providers):
    assert ask(999).status_code == 404 and providers[1].calls == []


@pytest.mark.parametrize("error, status", [
    (LLMError(502, "llm_auth_failed", "Gemini rejected the API key."), 502),
    (LLMError(429, "llm_rate_limited", "Gemini rate limit reached.", retryable=True, retry_after=30), 429),
    (LLMError(502, "llm_blocked", "Gemini declined to answer (SAFETY)."), 502),
])
def test_llm_failures_are_reported(repos, providers, monkeypatch, error, status):
    monkeypatch.setattr(config, "LLM_REQUEST_BUDGET_SECONDS", 0)
    providers[1].error = error
    r = ask(repos[0])
    assert r.status_code == status and r.json()["code"] == error.code
    if error.retry_after:
        assert r.json()["retry_after_seconds"] == 30


def test_truncated_answer_is_flagged(repos, providers):
    providers[1].finish = "MAX_TOKENS"
    assert ask(repos[0]).json()["truncated"] is True


def test_partial_embeddings_warning_is_passed_on(repos, providers):
    body = ask(repos[0]).json()
    assert body["partial"] is True and "Only 4 of 6 chunks are embedded" in body["message"]


def test_service_works_without_http(repos):
    a, _ = repos
    result = asyncio.run(rag.ask(a, "auth?", 2, embedding_provider=QueryProvider(), llm_provider=FakeLLM("No idea.")))
    assert result["answer"] == "No idea." and len(result["sources"]) == 2


def test_api_key_never_appears_in_responses(repos, providers, monkeypatch):
    secret = "AIzaSy-super-secret-llm-key"
    monkeypatch.setattr(config, "GEMINI_API_KEY", secret)
    assert secret not in ask(repos[0]).text and secret not in client.get("/api/health").text
    assert client.get("/api/health").json()["llm"]["api_key_configured"] is True
