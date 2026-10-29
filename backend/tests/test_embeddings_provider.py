"""The real Gemini client, exercised against Google-format responses via httpx.MockTransport."""

import asyncio
import json
import math

import httpx
import pytest

from app import config
from app.services.embeddings import EmbeddingError, GeminiEmbeddingProvider, get_provider

KEY = "test-key-123"
DIMS = 768


def provider(handler):
    return GeminiEmbeddingProvider(api_key=KEY, model="gemini-embedding-001", dimensions=DIMS,
                                   base_url="https://gemini.test/v1beta", timeout=5,
                                   transport=httpx.MockTransport(handler))


def embed(handler, texts=("a", "b")):
    return asyncio.run(provider(handler).embed_documents(list(texts)))


def vectors_response(request, scale=3.0):
    count = len(json.loads(request.content)["requests"])
    # Not unit length (as gemini-embedding-001 returns below 3072 dims): the client must normalize.
    return httpx.Response(200, json={"embeddings": [{"values": [scale * (i + 1)] + [1.0] * (DIMS - 1)} for i in range(count)]})


def google_error(status, message, status_text, details=()):
    return httpx.Response(status, json={"error": {"code": status, "message": message, "status": status_text,
                                                  "details": list(details)}})


def raises(handler) -> EmbeddingError:
    with pytest.raises(EmbeddingError) as info:
        embed(handler)
    assert KEY not in info.value.message
    return info.value


def test_request_format_and_normalized_output():
    seen = {}

    def handler(request):
        seen["url"], seen["key"], seen["body"] = str(request.url), request.headers["x-goog-api-key"], json.loads(request.content)
        return vectors_response(request)

    vectors = embed(handler, ["first chunk", "second chunk"])
    assert seen["url"] == "https://gemini.test/v1beta/models/gemini-embedding-001:batchEmbedContents"
    assert seen["key"] == KEY and KEY not in seen["url"]  # key in a header, never in the URL
    first = seen["body"]["requests"][0]
    assert first == {"model": "models/gemini-embedding-001", "content": {"parts": [{"text": "first chunk"}]},
                     "taskType": "RETRIEVAL_DOCUMENT", "outputDimensionality": DIMS}
    assert len(vectors) == 2 and all(len(v) == DIMS for v in vectors)
    assert all(math.isclose(math.sqrt(sum(x * x for x in v)), 1.0) for v in vectors)
    assert vectors[0] != vectors[1]  # order and content preserved


def test_invalid_api_key():
    err = raises(lambda r: google_error(400, "API key not valid. Please pass a valid API key.", "INVALID_ARGUMENT", [
        {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID", "domain": "googleapis.com"}]))
    assert (err.status_code, err.code, err.retryable) == (502, "embedding_auth_failed", False)
    assert "GEMINI_API_KEY" in err.message


def test_permission_denied():
    err = raises(lambda r: google_error(403, "Generative Language API has not been used in project 1.", "PERMISSION_DENIED"))
    assert err.code == "embedding_auth_failed" and "not been used" in err.message and not err.retryable


def test_rate_limit_uses_google_retry_delay():
    err = raises(lambda r: google_error(429, "You exceeded your current quota.", "RESOURCE_EXHAUSTED", [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}]))
    assert (err.status_code, err.code, err.retryable, err.retry_after) == (429, "embedding_rate_limited", True, 37.0)


def test_daily_quota_is_not_retried():
    err = raises(lambda r: google_error(429, "Quota exceeded.", "RESOURCE_EXHAUSTED", [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "EmbedContentRequestsPerDayPerProjectPerModel-FreeTier"}]}]))
    assert err.code == "embedding_rate_limited" and not err.retryable and "daily" in err.message


def test_server_error_is_retryable():
    err = raises(lambda r: google_error(503, "The model is overloaded.", "UNAVAILABLE"))
    assert (err.code, err.retryable) == ("embedding_provider_error", True)


def test_other_bad_request_is_not_retryable():
    err = raises(lambda r: google_error(400, "Request payload size exceeds the limit.", "INVALID_ARGUMENT"))
    assert err.code == "embedding_request_rejected" and "payload size" in err.message and not err.retryable


@pytest.mark.parametrize("exc, code", [(httpx.ConnectError("down"), "embedding_unreachable"),
                                       (httpx.ReadTimeout("slow"), "embedding_timeout")])
def test_network_failures_are_retryable(exc, code):
    def handler(request):
        raise exc
    err = raises(handler)
    assert err.code == code and err.retryable


@pytest.mark.parametrize("response, code", [
    (httpx.Response(200, text="<html>oops</html>"), "embedding_bad_response"),
    (httpx.Response(200, json={"unexpected": True}), "embedding_bad_response"),
    (httpx.Response(200, json={"embeddings": [{"values": [1.0] * DIMS}]}), "embedding_bad_response"),  # 1 for 2 inputs
    (httpx.Response(200, json={"embeddings": [{"values": [1.0] * DIMS}, {"values": ["x"] * DIMS}]}), "embedding_bad_response"),
    (httpx.Response(200, json={"embeddings": [{"values": [0.0] * DIMS}, {"values": [1.0] * DIMS}]}), "embedding_bad_response"),
    (httpx.Response(200, json={"embeddings": [{"values": [1.0] * 3072}, {"values": [1.0] * 3072}]}), "embedding_dimension_mismatch"),
])
def test_malformed_responses(response, code):
    err = raises(lambda r: response)
    assert err.code == code
    assert err.retryable == (code == "embedding_bad_response")


def test_batch_limit():
    with pytest.raises(ValueError):
        embed(vectors_response, ["x"] * 101)


def test_get_provider_requires_api_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    with pytest.raises(EmbeddingError) as info:
        get_provider()
    assert info.value.code == "embedding_not_configured" and "GEMINI_API_KEY" in info.value.message

    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "nope")
    with pytest.raises(EmbeddingError, match="Unknown EMBEDDING_PROVIDER"):
        get_provider()
