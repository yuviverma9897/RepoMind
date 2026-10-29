"""Semantic search against PostgreSQL + pgvector, using hand-made vectors with known similarities."""

import asyncio
import json
import math

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app import config
from app.db import database, store
from app.db.models import CodeChunk
from app.main import app
from app.services import embedding_service, search
from app.services.embeddings import EmbeddingError, EmbeddingProvider, GeminiEmbeddingProvider
from tests.test_db import META, chunk_row

client = TestClient(app)
DIMS = 768


def vec(*weights) -> list[float]:
    """Unit vector from weights on the first axes, e.g. vec(1, 0.5) = normalize(e0 + 0.5 e1)."""
    raw = list(weights) + [0.0] * (DIMS - len(weights))
    norm = math.sqrt(sum(v * v for v in raw))
    return [v / norm for v in raw]


QUERY = vec(1, 0.5)  # what the stand-in provider returns for every question


class QueryProvider(EmbeddingProvider):
    name, max_batch_size, dimensions = "fake", 100, DIMS

    def __init__(self, error=None):
        self.model = config.EMBEDDING_MODEL
        self.queries, self.error = [], error

    async def embed_documents(self, texts):
        raise AssertionError("search must embed the question with embed_query, not embed_documents")

    async def embed_query(self, text):
        self.queries.append(text)
        if self.error:
            raise self.error
        return QUERY


def add_repo(github_id, name, chunks):
    """chunks: list of (row overrides, vector or None, model or None)."""
    with database.session_scope() as s:
        repo = store.upsert_repository(s, {**META, "github_id": github_id, "name": name, "full_name": f"o/{name}"})
        store.insert_chunks(s, [chunk_row(repo.id, i, **over) for i, (over, _, _) in enumerate(chunks)])
        ids = list(s.scalars(select(CodeChunk.id).where(CodeChunk.repository_id == repo.id).order_by(CodeChunk.id)))
    by_model = {}
    for chunk_id, (_, vector, model) in zip(ids, chunks):
        if vector is not None:
            by_model.setdefault(model or config.EMBEDDING_MODEL, []).append((chunk_id, vector))
    with database.session_scope() as s:
        for model, items in by_model.items():
            store.save_embeddings(s, items, model)
    return repo.id


@pytest.fixture
def repos(db):
    """Repository A with known vectors, and repository B holding a *perfect* match for the query."""
    a = add_repo(101, "alpha", [
        ({"path": "src/auth.py", "symbol_name": "login", "start_line": 10, "end_line": 32,
          "content": "def login(user):\n    ..."}, vec(1), None),                 # similarity 0.8944
        ({"path": "src/db.py", "symbol_name": "connect", "start_line": 1, "end_line": 9,
          "content": "def connect():\n    ..."}, vec(0, 1), None),               # 0.4472
        ({"path": "src/blog.py", "symbol_name": "index"}, vec(0, 0, 1), None),     # 0.0
        ({"path": "src/auth.py", "symbol_name": "logout", "start_line": 40, "end_line": 50}, vec(1, -1), None),  # 0.3162
        ({"path": "src/old.py", "symbol_name": "stale"}, QUERY, "some-old-model"),  # wrong model: never compared
        ({"path": "src/new.py", "symbol_name": "not_embedded_yet"}, None, None),
    ])
    b = add_repo(202, "beta", [({"path": "other/secret.py", "symbol_name": "perfect"}, QUERY, None)])
    return a, b


@pytest.fixture
def provider(monkeypatch):
    p = QueryProvider()
    monkeypatch.setattr(embedding_service, "get_provider", lambda: p)
    return p


def post(repo_id, query="Where is user authentication handled?", top_k=8, **extra):
    return client.post(f"/api/repos/{repo_id}/search", json={"query": query, "top_k": top_k, **extra})


def test_ranks_by_cosine_similarity_with_exact_scores(repos, provider):
    a, _ = repos
    body = post(a).json()
    assert provider.queries == ["Where is user authentication handled?"]  # question embedded once, as a query
    got = [(r["symbol_name"], r["similarity"]) for r in body["results"]]
    assert got == [("login", 0.8944), ("connect", 0.4472), ("logout", 0.3162), ("index", 0.0)]
    assert [r["rank"] for r in body["results"]] == [1, 2, 3, 4]
    assert body["metric"] == "cosine" and body["model"] == config.EMBEDDING_MODEL


def test_results_never_include_other_repositories_or_models(repos, provider):
    a, b = repos
    results = post(a, top_k=50).json()["results"]
    assert {r["repository_id"] for r in results} == {a}
    assert "perfect" not in {r["symbol_name"] for r in results}  # B's similarity-1.0 chunk is excluded
    assert "stale" not in {r["symbol_name"] for r in results}  # vector from another model is excluded
    only_b = post(b).json()["results"]
    assert [(r["symbol_name"], r["similarity"]) for r in only_b] == [("perfect", 1.0)]


def test_result_metadata_is_complete(repos, provider):
    a, _ = repos
    top = post(a, top_k=1).json()["results"][0]
    assert top == {
        "rank": 1, "chunk_id": top["chunk_id"], "repository_id": a, "file_path": "src/auth.py", "language": "python",
        "chunk_type": "function", "symbol_name": "login", "parent_symbol": None, "start_line": 10, "end_line": 32,
        "part": None, "total_parts": None, "similarity": 0.8944, "content": "def login(user):\n    ...",
    }


def test_partial_embeddings_are_flagged(repos, provider):
    a, _ = repos
    body = post(a).json()
    assert body["partial"] is True and body["searched_chunks"] == 4 and body["total_chunks"] == 6
    assert "Only 4 of 6 chunks are embedded" in body["message"]


def test_top_k_is_bounded(repos, provider):
    a, _ = repos
    assert len(post(a, top_k=2).json()["results"]) == 2
    assert len(client.post(f"/api/repos/{a}/search", json={"query": "auth"}).json()["results"]) == 4  # default 8
    for bad in (0, -1, 51, 10_000):
        r = post(a, top_k=bad)
        assert r.status_code == 400 and r.json()["code"] == "invalid_top_k"
    assert len(provider.queries) == 2  # only the two valid requests reached the provider


@pytest.mark.parametrize("query, code", [("", "empty_query"), ("   \n\t ", "empty_query"), ("x" * 1001, "query_too_long")])
def test_invalid_queries_are_rejected(repos, provider, query, code):
    r = post(repos[0], query=query)
    assert r.status_code == 400 and r.json()["code"] == code
    assert provider.queries == []


def test_query_is_trimmed(repos, provider):
    assert post(repos[0], query="  login flow  ").json()["query"] == "login flow"


def test_unknown_repository(db, provider):
    r = post(999)
    assert r.status_code == 404 and r.json()["code"] == "repository_not_indexed"


def test_repository_without_chunks(db, provider):
    repo_id = add_repo(303, "empty", [])
    r = post(repo_id)
    assert r.status_code == 409 and r.json()["code"] == "repository_has_no_chunks"


def test_repository_without_embeddings(db, provider):
    repo_id = add_repo(404, "plain", [({}, None, None), ({}, None, None)])
    r = post(repo_id)
    assert r.status_code == 409 and r.json()["code"] == "embeddings_not_ready"
    assert r.json()["detail"] == "Repository embeddings are not ready. Generate embeddings before searching."
    assert provider.queries == []  # no API call wasted


def test_embeddings_from_another_model_only(db, provider):
    repo_id = add_repo(505, "oldmodel", [({}, vec(1), "some-old-model")])
    r = post(repo_id)
    assert r.status_code == 409 and "different model" in r.json()["detail"]


def test_missing_api_key(repos, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    r = post(repos[0])
    assert r.status_code == 503 and r.json()["code"] == "embedding_not_configured"


@pytest.mark.parametrize("error, status", [
    (EmbeddingError(502, "embedding_auth_failed", "Gemini rejected the API key."), 502),
    (EmbeddingError(429, "embedding_rate_limited", "Gemini rate limit reached.", retryable=True, retry_after=99), 429),
    (EmbeddingError(503, "embedding_unreachable", "Could not connect.", retryable=True), 503),
])
def test_embedding_failures_are_reported(repos, monkeypatch, error, status):
    monkeypatch.setattr(config, "EMBEDDING_REQUEST_BUDGET_SECONDS", 0)  # no waiting in tests
    p = QueryProvider(error=error)
    monkeypatch.setattr(embedding_service, "get_provider", lambda: p)
    r = post(repos[0])
    assert r.status_code == status and r.json()["code"] == error.code


def test_transient_failure_is_retried(repos, monkeypatch):
    p = QueryProvider(error=EmbeddingError(503, "embedding_unreachable", "blip", retryable=True, retry_after=0.01))
    original = p.embed_query

    async def flaky(text):
        try:
            return await original(text)
        finally:
            p.error = None  # only the first attempt fails
    p.embed_query = flaky
    monkeypatch.setattr(embedding_service, "get_provider", lambda: p)
    assert post(repos[0]).status_code == 200 and len(p.queries) == 2


def test_database_failure_during_search(repos, provider, monkeypatch):
    def broken(*args, **kwargs):
        raise OperationalError("SELECT", {}, Exception("connection lost"))
    monkeypatch.setattr(store, "search_chunks", broken)
    r = post(repos[0])
    assert r.status_code == 503 and r.json()["code"] == "database_unavailable"


def test_sql_filters_by_repository_and_orders_by_cosine_distance(repos, provider, db):
    """Isolation and ranking happen inside PostgreSQL, not in Python."""
    statements = []
    listener = lambda conn, cursor, sql, params, context, many: statements.append(sql)
    event.listen(db, "before_cursor_execute", listener)
    try:
        post(repos[0])
    finally:
        event.remove(db, "before_cursor_execute", listener)
    sql = next(s for s in statements if "<=>" in s)
    where, order = sql.split("WHERE", 1)[1].split("ORDER BY")
    assert "code_chunks.repository_id = " in where and "code_chunks.embedding_model = " in where
    assert "code_chunks.embedding <=> " in order and "LIMIT" in order


def test_gemini_query_embedding_uses_code_retrieval_task():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [{"values": [2.0] * DIMS}]})

    p = GeminiEmbeddingProvider(api_key="k", model="gemini-embedding-001", dimensions=DIMS,
                                base_url="https://gemini.test/v1beta", timeout=5, transport=httpx.MockTransport(handler))
    vector = asyncio.run(p.embed_query("where is auth?"))
    assert seen["body"]["requests"] == [{"model": "models/gemini-embedding-001",
                                         "content": {"parts": [{"text": "where is auth?"}]},
                                         "taskType": "CODE_RETRIEVAL_QUERY", "outputDimensionality": DIMS}]
    assert len(vector) == DIMS and math.isclose(sum(v * v for v in vector), 1.0)
