"""Phase 9: citation validation, evidence levels and source metadata (stub LLM, real PostgreSQL)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import embedding_service, llm
from app.services.citations import assess_evidence, mark_cited, process_citations
from tests.test_rag import FakeLLM
from tests.test_search import QueryProvider, add_repo, repos, vec  # noqa: F401 (fixture)

client = TestClient(app)
SOURCES = [{"source_id": f"S{i}", "similarity": s} for i, s in enumerate([0.8, 0.7, 0.6, 0.5], start=1)]


# ---------------------------------------------------------------- unit: process_citations

def test_one_valid_source():
    r = process_citations("Login is handled by `login` [S1].", SOURCES[:1])
    assert r == {"text": "Login is handled by `login` [S1].", "cited": ["S1"], "invalid": []}


def test_multiple_sources_are_normalized_in_order_of_first_use():
    r = process_citations("A [S3][S1]. B [S2, S4]. C [s1; S3].", SOURCES)
    assert r["text"] == "A [S3][S1]. B [S2][S4]. C [S1][S3]."
    assert r["cited"] == ["S3", "S1", "S2", "S4"] and r["invalid"] == []


def test_invalid_citations_are_removed_and_reported():
    r = process_citations("Uses a cache [S99]. Sessions [S1, S7] and tokens [S0].", SOURCES[:2])
    assert r["text"] == "Uses a cache. Sessions [S1] and tokens."
    assert r["cited"] == ["S1"] and r["invalid"] == ["S99", "S7", "S0"]


def test_non_citation_brackets_are_untouched():
    text = "See [the docs](https://x) and `items[S1a]`, [Section 2], [1]."
    assert process_citations(text, SOURCES)["text"] == text


def test_mark_cited():
    marked = mark_cited(SOURCES[:3], ["S2"])
    assert [s["cited"] for s in marked] == [False, True, False]


@pytest.mark.parametrize("sources, cited, level", [
    (SOURCES, ["S1"], "supported"),
    ([{"source_id": "S1", "similarity": 0.31}], ["S1"], "weak"),  # no strong source, even if cited
    (SOURCES, [], "uncited"),
    ([], [], "none"),
])
def test_evidence_levels(sources, cited, level):
    evidence = assess_evidence(sources, cited, threshold=0.5)
    assert evidence["level"] == level
    assert (evidence["message"] is None) == (level == "supported")


# ---------------------------------------------------------------- API

@pytest.fixture
def stub(monkeypatch):
    model = FakeLLM()
    monkeypatch.setattr(embedding_service, "get_provider", lambda: QueryProvider())
    monkeypatch.setattr(llm, "get_llm", lambda: model)
    return model


def ask(repo_id, question="Where is login?"):
    r = client.post(f"/api/repos/{repo_id}/ask", json={"question": question, "top_k": 4})
    assert r.status_code == 200, r.text
    return r.json()


def test_api_source_metadata_and_line_ranges(repos, stub):
    a, _ = repos
    stub.text = "Login [S1], connection [S2]."
    body = ask(a)
    first = body["sources"][0]
    assert {k: first[k] for k in ("source_id", "repository_id", "file_path", "language", "symbol_name", "chunk_type",
                                  "start_line", "end_line", "similarity", "cited")} == {
        "source_id": "S1", "repository_id": a, "file_path": "src/auth.py", "language": "python", "symbol_name": "login",
        "chunk_type": "function", "start_line": 10, "end_line": 32, "similarity": 0.8944, "cited": True}
    assert isinstance(first["chunk_id"], int)
    assert all(1 <= s["start_line"] <= s["end_line"] for s in body["sources"])
    assert [s["cited"] for s in body["sources"]] == [True, True, False, False]
    assert body["citations"] == {"cited": ["S1", "S2"], "invalid": []}
    assert body["evidence"]["level"] == "supported" and body["evidence"]["best_similarity"] == 0.8944


def test_api_invalid_citation(repos, stub):
    stub.text = "Handled in `login` [S1][S99]. Also in a missing file [S42]."
    body = ask(repos[0])
    assert body["answer"] == "Handled in `login` [S1]. Also in a missing file."
    assert body["citations"] == {"cited": ["S1"], "invalid": ["S99", "S42"]}


def test_api_no_strong_source(db, stub):
    repo_id = add_repo(303, "weak", [({"path": "a.py"}, vec(0, 0, 1), None), ({"path": "b.py"}, vec(-1, 0, 1), None)])
    stub.text = "The provided code does not show this."
    body = ask(repo_id, "How are payments processed?")
    assert body["evidence"]["level"] == "weak" and "weak" in body["evidence"]["message"]
    assert "weakly related" in stub.calls[0][1]  # the model is told before answering
    assert body["citations"] == {"cited": [], "invalid": []}


def test_api_uncited_answer(repos, stub):
    stub.text = "It probably uses sessions."
    body = ask(repos[0])
    assert body["evidence"]["level"] == "uncited" and not any(s["cited"] for s in body["sources"])
    assert "weakly related" not in stub.calls[0][1]
