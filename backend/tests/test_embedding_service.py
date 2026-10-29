"""Embedding pipeline against PostgreSQL + pgvector, with a stand-in provider (no external calls)."""

import asyncio
import hashlib
import math

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import config
from app.db import database, store
from app.main import app
from app.services import embedding_service
from app.services.embedding_service import embed_repository, prepare_text
from app.services.embeddings import EmbeddingError, EmbeddingProvider
from tests.test_db import META, chunk_row
from tests.test_indexing import FILES, analyze, fake_github  # noqa: F401 (fixture)

client = TestClient(app)


class FakeProvider(EmbeddingProvider):
    """Deterministic unit vectors. `failures` maps call number (1-based) -> error to raise."""

    name = "fake"
    max_batch_size = 100

    def __init__(self, failures=None, model=None):
        self.model = model or config.EMBEDDING_MODEL
        self.dimensions = 768
        self.calls: list[list[str]] = []
        self.failures = dict(failures or {})

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        error = self.failures.pop(len(self.calls), None)
        if error:
            raise error
        vectors = []
        for t in texts:
            seed = hashlib.sha256(t.encode()).digest()
            raw = [(seed[i % 32] - 127.5) / 127.5 + 0.01 for i in range(self.dimensions)]
            norm = math.sqrt(sum(v * v for v in raw))
            vectors.append([v / norm for v in raw])
        return vectors

    async def embed_query(self, text):
        self.queries = getattr(self, "queries", []) + [text]
        return (await self.embed_documents([text]))[0]


def make_repo(n=12, **overrides):
    with database.session_scope() as s:
        repo = store.upsert_repository(s, META)
        store.insert_chunks(s, [chunk_row(repo.id, i, **overrides) for i in range(n)])
        return repo.id


def run(repo_id, provider):
    return asyncio.run(embed_repository(repo_id, provider=provider))


@pytest.fixture
def small_batches(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_BATCH_SIZE", 5)


def test_prepare_text():
    class C:
        file_path, language, chunk_type = "src/auth/session.py", "python", "method"
        symbol_name, parent_symbol, part, total_parts = "refresh", "SessionStore", 2, 3
        content = "def refresh(self):\n    ..."
    assert prepare_text(C) == ("File: src/auth/session.py\nLanguage: python\nSymbol: method SessionStore.refresh\n"
                               "Part 2 of 3\n\ndef refresh(self):\n    ...")
    C.symbol_name = C.parent_symbol = C.part = None
    C.chunk_type = "imports"
    assert prepare_text(C).startswith("File: src/auth/session.py\nLanguage: python\nType: imports\n\n")


def test_embeds_all_chunks_in_batches_and_stores_vectors(db, small_batches):
    repo_id = make_repo(12)
    provider = FakeProvider()
    result = run(repo_id, provider)
    assert [len(c) for c in provider.calls] == [5, 5, 2]  # batched, not one request per chunk
    assert result["status"] == "complete" and result["newly_embedded"] == 12 and result["embedded_chunks"] == 12
    assert (result["remaining_chunks"], result["failed_chunks"], result["progress_percent"]) == (0, 0, 100.0)

    with db.connect() as conn:
        rows = conn.execute(text("SELECT vector_dims(embedding), vector_norm(embedding), embedding_model, embedded_at "
                                 "FROM code_chunks WHERE repository_id = :r"), {"r": repo_id}).all()
        assert len(rows) == 12
        assert all(dims == 768 and math.isclose(norm, 1.0, rel_tol=1e-5) and model == config.EMBEDDING_MODEL and at
                   for dims, norm, model, at in rows)
        # Stored as real pgvector values: similarity operators work on them.
        nearest = conn.execute(text(
            "SELECT b.id FROM code_chunks a JOIN code_chunks b ON b.id <> a.id AND b.repository_id = a.repository_id "
            "WHERE a.id = (SELECT min(id) FROM code_chunks) ORDER BY a.embedding <=> b.embedding LIMIT 1")).scalar()
        assert nearest is not None


def test_second_call_does_not_regenerate(db, small_batches):
    repo_id = make_repo(7)
    run(repo_id, FakeProvider())
    with db.connect() as conn:
        before = conn.execute(text("SELECT id, embedded_at FROM code_chunks ORDER BY id")).all()
    provider = FakeProvider()
    result = run(repo_id, provider)
    assert provider.calls == []
    assert (result["status"], result["newly_embedded"], result["already_embedded"]) == ("complete", 0, 7)
    with db.connect() as conn:
        assert conn.execute(text("SELECT id, embedded_at FROM code_chunks ORDER BY id")).all() == before


def test_failed_batch_keeps_earlier_batches_and_resumes(db, small_batches):
    repo_id = make_repo(12)
    broken = FakeProvider(failures={2: EmbeddingError(502, "embedding_request_rejected", "rejected")})
    result = run(repo_id, broken)
    assert result["status"] == "interrupted"
    assert (result["newly_embedded"], result["failed_chunks"], result["remaining_chunks"]) == (5, 5, 7)
    assert result["error"]["code"] == "embedding_request_rejected"
    assert embedding_service.embedding_status(repo_id)["embedded_chunks"] == 5  # first batch survived

    first_batch = set(broken.calls[0])
    healthy = FakeProvider()
    resumed = run(repo_id, healthy)
    assert resumed["status"] == "complete" and resumed["newly_embedded"] == 7
    assert first_batch.isdisjoint(t for call in healthy.calls for t in call)  # nothing embedded twice


def test_transient_error_is_retried_within_the_call(db, small_batches):
    repo_id = make_repo(5)
    provider = FakeProvider(failures={1: EmbeddingError(429, "embedding_rate_limited", "slow down",
                                                        retryable=True, retry_after=0.01)})
    result = run(repo_id, provider)
    assert result["status"] == "complete" and len(provider.calls) == 2


def test_time_budget_returns_progress_and_next_call_continues(db, small_batches, monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_REQUEST_BUDGET_SECONDS", 0)
    repo_id = make_repo(12)
    progress = []
    while True:
        result = run(repo_id, FakeProvider())
        progress.append((result["status"], result["embedded_chunks"]))
        if result["status"] == "complete":
            break
    assert progress == [("in_progress", 5), ("in_progress", 10), ("complete", 12)]


def test_empty_chunks_are_skipped_not_sent(db, small_batches):
    repo_id = make_repo(3)
    with database.session_scope() as s:
        store.insert_chunks(s, [chunk_row(repo_id, 50, content="   \n\t")])
    provider = FakeProvider()
    result = run(repo_id, provider)
    assert all(t.strip().endswith("pass") for call in provider.calls for t in call)
    assert (result["embedded_chunks"], result["skipped_chunks"], result["remaining_chunks"]) == (3, 1, 0)
    assert result["status"] == "complete" and result["progress_percent"] == 100.0


def test_model_change_marks_vectors_outdated_and_reembeds(db, small_batches, monkeypatch):
    repo_id = make_repo(4)
    run(repo_id, FakeProvider())
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "another-model")
    status = embedding_service.embedding_status(repo_id)
    assert (status["embedded_chunks"], status["outdated_chunks"], status["remaining_chunks"]) == (0, 4, 4)
    assert run(repo_id, FakeProvider())["newly_embedded"] == 4


def test_reanalysis_only_leaves_changed_files_pending(db, fake_github):
    repo_id = analyze()["repository_id"]
    run(repo_id, FakeProvider())
    fake_github.files = {**FILES, "src/util.js": b"export function add(a, b) {\n  return b + a;\n}\n"}
    analyze()
    status = embedding_service.embedding_status(repo_id)
    assert status["remaining_chunks"] == 1  # only the changed file's chunk; the rest kept their vectors
    assert status["embedded_chunks"] == status["total_chunks"] - 1


# ---------------------------------------------------------------- HTTP endpoints

@pytest.fixture
def use_provider(monkeypatch):
    def install(provider):
        monkeypatch.setattr(embedding_service, "get_provider", lambda: provider)
    return install


def test_endpoints_happy_path(db, use_provider):
    repo_id = make_repo(6)
    use_provider(FakeProvider())
    before = client.get(f"/api/repos/{repo_id}/embedding-status").json()
    assert (before["total_chunks"], before["embedded_chunks"], before["progress_percent"]) == (6, 0, 0.0)
    result = client.post(f"/api/repos/{repo_id}/embeddings")
    assert result.status_code == 200 and result.json()["status"] == "complete"
    after = client.get(f"/api/repos/{repo_id}/embedding-status").json()
    assert (after["embedded_chunks"], after["remaining_chunks"], after["progress_percent"]) == (6, 0, 100.0)


def test_missing_api_key_gives_clear_error(db, monkeypatch):
    repo_id = make_repo(2)
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    response = client.post(f"/api/repos/{repo_id}/embeddings")
    assert response.status_code == 503
    assert response.json()["code"] == "embedding_not_configured" and "GEMINI_API_KEY" in response.json()["detail"]
    assert client.get(f"/api/repos/{repo_id}/embedding-status").json()["provider_configured"] is False


def test_api_key_never_appears_in_responses(db, monkeypatch, use_provider):
    secret = "AIzaSy-very-secret-test-key"
    monkeypatch.setattr(config, "GEMINI_API_KEY", secret)
    repo_id = make_repo(2)
    use_provider(FakeProvider())
    bodies = [client.get("/api/health").text, client.get(f"/api/repos/{repo_id}/embedding-status").text,
              client.post(f"/api/repos/{repo_id}/embeddings").text]
    assert all(secret not in body for body in bodies)
    assert client.get("/api/health").json()["embeddings"]["api_key_configured"] is True


def test_invalid_key_on_first_batch_saves_nothing(db, use_provider, small_batches):
    repo_id = make_repo(6)
    use_provider(FakeProvider(failures={1: EmbeddingError(502, "embedding_auth_failed", "Gemini rejected the API key.")}))
    response = client.post(f"/api/repos/{repo_id}/embeddings")
    assert response.status_code == 502 and response.json()["code"] == "embedding_auth_failed"
    assert embedding_service.embedding_status(repo_id)["embedded_chunks"] == 0


def test_rate_limit_longer_than_budget_reports_retry_after(db, use_provider):
    repo_id = make_repo(3)
    use_provider(FakeProvider(failures={1: EmbeddingError(429, "embedding_rate_limited", "Gemini rate limit reached.",
                                                          retryable=True, retry_after=40)}))
    response = client.post(f"/api/repos/{repo_id}/embeddings")
    assert response.status_code == 429
    assert response.json()["retry_after_seconds"] == 40 and response.headers["Retry-After"] == "40"


def test_dimension_mismatch_is_caught_before_calling_the_provider(db, use_provider, monkeypatch):
    repo_id = make_repo(2)
    provider = FakeProvider()
    use_provider(provider)
    monkeypatch.setattr(config, "EMBEDDING_DIMENSIONS", 1536)
    response = client.post(f"/api/repos/{repo_id}/embeddings")
    assert response.status_code == 500 and response.json()["code"] == "embedding_dimension_mismatch"
    assert "vector(768)" in response.json()["detail"] and provider.calls == []


def test_concurrent_run_is_rejected(db, use_provider):
    repo_id = make_repo(2)
    use_provider(FakeProvider())
    embedding_service._running.add(repo_id)
    try:
        response = client.post(f"/api/repos/{repo_id}/embeddings")
        assert response.status_code == 409 and response.json()["code"] == "embedding_in_progress"
    finally:
        embedding_service._running.discard(repo_id)


def test_unknown_repository(db):
    assert client.post("/api/repos/999/embeddings").status_code == 404
    assert client.get("/api/repos/999/embedding-status").json()["code"] == "repository_not_indexed"
