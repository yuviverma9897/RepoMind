"""LLM providers. The only module that knows a text-generation API.

    llm = get_llm()
    result = await llm.generate(system="...", prompt="...")   # -> LLMResult(text, finish_reason, usage)

To support another provider, subclass LLMProvider and register it in get_llm().
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from app import config
from app.services import secrets
from app.services.embeddings import MODEL_NAME_PATTERN, EmbeddingError, google_api_error, with_retries


class LLMError(EmbeddingError):
    """Same shape (and HTTP error handler) as EmbeddingError, with llm_* codes."""


@dataclass
class LLMResult:
    text: str
    finish_reason: str  # STOP | MAX_TOKENS | ...
    model: str
    usage: dict = field(default_factory=dict)  # input_tokens, output_tokens, thinking_tokens

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "MAX_TOKENS"


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    async def generate(self, system: str, prompt: str, *, json_output: bool = False) -> LLMResult:
        """One non-streaming completion (json_output: ask for a JSON response). Raises LLMError on failure."""


class GeminiLLMProvider(LLMProvider):
    """Google Gemini API via REST: models/{model}:generateContent."""

    name = "gemini"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float, thinking_level: str,
                 max_output_tokens: int, transport: httpx.AsyncBaseTransport | None = None):
        if not MODEL_NAME_PATTERN.match(model):
            raise LLMError(503, "llm_not_configured", f"Invalid LLM_MODEL '{model}'.")
        self._api_key = api_key
        self.model = model
        self._base_url = base_url
        self._timeout = timeout
        self._thinking_level = thinking_level
        self._max_output_tokens = max_output_tokens
        self._transport = transport

    async def generate(self, system: str, prompt: str, *, json_output: bool = False) -> LLMResult:
        models_to_try = [self.model]
        for fb in ("gemini-3.5-flash", "gemini-flash-latest", "gemini-3-flash-preview"):
            if fb not in models_to_try:
                models_to_try.append(fb)

        last_error = None
        for current_model in models_to_try:
            generation_config = {"maxOutputTokens": self._max_output_tokens}
            if json_output:
                generation_config["responseMimeType"] = "application/json"
            if self._thinking_level and "3.8" in current_model:
                generation_config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
            }
            try:
                async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout, transport=self._transport) as client:
                    response = await client.post(f"/models/{current_model}:generateContent", json=body,
                                                 headers={"x-goog-api-key": self._api_key})
                if response.status_code >= 400:
                    err = google_api_error(response, self._api_key, prefix="llm", error_cls=LLMError)
                    if response.status_code in (404, 503, 504) and current_model != models_to_try[-1]:
                        last_error = err
                        continue
                    raise err
                try:
                    data = response.json()
                except ValueError:
                    raise LLMError(502, "llm_bad_response", "The language model returned invalid JSON.", retryable=True)
                return self._parse(data)
            except (httpx.TimeoutException, LLMError, httpx.RequestError) as exc:
                last_error = exc
                if current_model != models_to_try[-1]:
                    await asyncio.sleep(0.5)
                    continue
                if isinstance(exc, httpx.TimeoutException):
                    raise LLMError(504, "llm_timeout", "The language model did not respond in time.", retryable=True)
                if isinstance(exc, httpx.RequestError):
                    raise LLMError(503, "llm_unreachable", "Could not connect to the language model provider.", retryable=True)
                raise exc

        if last_error:
            raise last_error

    def _parse(self, data) -> LLMResult:
        if not isinstance(data, dict):
            raise LLMError(502, "llm_bad_response", "The language model returned an unexpected response.", retryable=True)
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            raise LLMError(502, "llm_blocked", f"Gemini declined to process this request ({blocked}).")
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise LLMError(502, "llm_bad_response", "The language model returned no answer.", retryable=True)
        candidate = candidates[0]
        finish = str(candidate.get("finishReason") or "UNKNOWN")
        parts = (candidate.get("content") or {}).get("parts") or []
        # Thought-summary parts (thought: true) are internal reasoning, not the answer.
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()
        if not text:
            if finish in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
                raise LLMError(502, "llm_blocked", f"Gemini declined to answer ({finish}).")
            if finish == "MAX_TOKENS":
                raise LLMError(502, "llm_output_limit", "The model used its whole output budget before answering. "
                               "Increase LLM_MAX_OUTPUT_TOKENS or lower LLM_THINKING_LEVEL.")
            raise LLMError(502, "llm_bad_response", f"The language model returned an empty answer ({finish}).",
                           retryable=True)
        usage = data.get("usageMetadata") or {}
        return LLMResult(
            text=text,
            finish_reason=finish,
            model=str(data.get("modelVersion") or self.model),
            usage={
                "input_tokens": usage.get("promptTokenCount"),
                "output_tokens": usage.get("candidatesTokenCount"),
                "thinking_tokens": usage.get("thoughtsTokenCount"),
            },
        )


def llm_configured() -> bool:
    return config.LLM_PROVIDER == "gemini" and config.GEMINI_API_KEY is not None


def get_llm() -> LLMProvider:
    if config.LLM_PROVIDER != "gemini":
        raise LLMError(503, "llm_not_configured", f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}'. Supported: gemini.")
    if not config.GEMINI_API_KEY:
        raise LLMError(503, "llm_not_configured",
                       "GEMINI_API_KEY is not set. Create a key at https://aistudio.google.com/apikey "
                       "and add it to backend/.env, then restart the backend.")
    return GeminiLLMProvider(
        api_key=config.GEMINI_API_KEY,
        model=config.LLM_MODEL,
        base_url=config.GEMINI_API_URL,
        timeout=config.LLM_TIMEOUT_SECONDS,
        thinking_level=config.LLM_THINKING_LEVEL,
        max_output_tokens=config.LLM_MAX_OUTPUT_TOKENS,
    )


async def generate(system: str, prompt: str, provider: LLMProvider | None = None, *,
                   json_output: bool = False) -> LLMResult:
    """Generate with the configured provider, retrying transient failures (overload, network).

    Every prompt and every answer passes through secret masking: repository secrets are never
    sent to the provider or returned to the client.
    """
    provider = provider or get_llm()
    system, prompt = secrets.mask(system), secrets.mask(prompt)
    kwargs = {"json_output": True} if json_output else {}
    deadline = time.monotonic() + config.LLM_REQUEST_BUDGET_SECONDS
    result = await with_retries(lambda: provider.generate(system, prompt, **kwargs), deadline, config.LLM_MAX_RETRIES)
    result.text = secrets.mask(result.text)
    return result
