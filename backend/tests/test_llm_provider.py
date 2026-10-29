"""The real Gemini generateContent client, exercised against Google-format responses via httpx.MockTransport."""

import asyncio
import json

import httpx
import pytest

from app import config
from app.services import llm
from app.services.llm import GeminiLLMProvider, LLMError, LLMResult

KEY = "test-llm-key-456"


def provider(handler, thinking_level="low"):
    return GeminiLLMProvider(api_key=KEY, model="gemini-3.8-flash", base_url="https://gemini.test/v1beta", timeout=5,
                             thinking_level=thinking_level, max_output_tokens=4096, transport=httpx.MockTransport(handler))


def answer(text="Sessions are signed cookies [S1].", finish="STOP", extra_parts=(), **top):
    return httpx.Response(200, json={
        "candidates": [{"content": {"role": "model", "parts": [*extra_parts, {"text": text}] if text else list(extra_parts)},
                        "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 85, "thoughtsTokenCount": 40,
                          "totalTokenCount": 1325},
        "modelVersion": "gemini-3.8-flash", **top})


def run(handler, **kw) -> LLMResult:
    return asyncio.run(provider(handler, **kw).generate("SYSTEM RULES", "USER PROMPT"))


def raises(handler) -> LLMError:
    with pytest.raises(LLMError) as info:
        run(handler)
    assert KEY not in info.value.message
    return info.value


def test_request_format():
    seen = {}

    def handler(request):
        seen["url"], seen["key"], seen["body"] = str(request.url), request.headers["x-goog-api-key"], json.loads(request.content)
        return answer()

    run(handler)
    assert seen["url"] == "https://gemini.test/v1beta/models/gemini-3.8-flash:generateContent"
    assert seen["key"] == KEY and KEY not in seen["url"]
    assert seen["body"] == {
        "systemInstruction": {"parts": [{"text": "SYSTEM RULES"}]},
        "contents": [{"role": "user", "parts": [{"text": "USER PROMPT"}]}],
        "generationConfig": {"maxOutputTokens": 4096, "thinkingConfig": {"thinkingLevel": "low"}},
    }


def test_thinking_level_can_be_left_to_the_model_default():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return answer()

    run(handler, thinking_level="")
    assert "thinkingConfig" not in seen["body"]["generationConfig"]


def test_parses_answer_skipping_thought_parts():
    result = run(lambda r: answer("The answer [S1].", extra_parts=[{"text": "internal reasoning", "thought": True}]))
    assert result.text == "The answer [S1]."
    assert (result.finish_reason, result.truncated, result.model) == ("STOP", False, "gemini-3.8-flash")
    assert result.usage == {"input_tokens": 1200, "output_tokens": 85, "thinking_tokens": 40}


def test_output_limit_with_text_is_flagged_as_truncated():
    result = run(lambda r: answer("Partial answer", finish="MAX_TOKENS"))
    assert result.truncated and result.text == "Partial answer"


def test_output_limit_without_text():
    err = raises(lambda r: answer(None, finish="MAX_TOKENS", extra_parts=[{"text": "thinking...", "thought": True}]))
    assert err.code == "llm_output_limit" and "LLM_MAX_OUTPUT_TOKENS" in err.message


def test_blocked_answers():
    assert raises(lambda r: answer(None, finish="SAFETY")).code == "llm_blocked"
    err = raises(lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}))
    assert err.code == "llm_blocked" and "PROHIBITED_CONTENT" in err.message and not err.retryable


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>"),
    httpx.Response(200, json={"candidates": []}),
    httpx.Response(200, json=["not", "an", "object"]),
    httpx.Response(200, json={"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}),
])
def test_malformed_responses_are_retryable(response):
    err = raises(lambda r: response)
    assert err.code == "llm_bad_response" and err.retryable


def test_google_errors_use_llm_codes():
    invalid = raises(lambda r: httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid.",
        "status": "INVALID_ARGUMENT", "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]}}))
    assert (invalid.status_code, invalid.code, invalid.retryable) == (502, "llm_auth_failed", False)

    limited = raises(lambda r: httpx.Response(429, json={"error": {"code": 429, "message": "Quota exceeded.", "status": "RESOURCE_EXHAUSTED",
        "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"}]}}))
    assert (limited.code, limited.retryable, limited.retry_after) == ("llm_rate_limited", True, 12.0)

    overloaded = raises(lambda r: httpx.Response(503, json={"error": {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}}))
    assert (overloaded.code, overloaded.retryable) == ("llm_provider_error", True)

    rejected = raises(lambda r: httpx.Response(400, json={"error": {"code": 400, "message": "Unsupported thinking level", "status": "INVALID_ARGUMENT"}}))
    assert rejected.code == "llm_request_rejected" and "Unsupported thinking level" in rejected.message


@pytest.mark.parametrize("exc, code", [(httpx.ConnectError("down"), "llm_unreachable"), (httpx.ReadTimeout("slow"), "llm_timeout")])
def test_network_failures(exc, code):
    def handler(request):
        raise exc
    err = raises(handler)
    assert err.code == code and err.retryable


def test_generate_retries_transient_errors(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE",
                "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "0.01s"}]}})
        return answer()

    result = asyncio.run(llm.generate("s", "p", provider=provider(handler)))
    assert result.text.endswith("[S1].") and len(calls) == 2


def test_get_llm_requires_api_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    with pytest.raises(LLMError) as info:
        llm.get_llm()
    assert info.value.code == "llm_not_configured" and "GEMINI_API_KEY" in info.value.message
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "LLM_PROVIDER", "other")
    with pytest.raises(LLMError, match="Unknown LLM_PROVIDER"):
        llm.get_llm()
