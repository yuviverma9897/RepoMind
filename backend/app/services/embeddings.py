"""Embedding providers. This is the only module that knows a provider's HTTP API.

The rest of the application depends on the small EmbeddingProvider interface:

    provider = get_provider()
    vectors = await provider.embed_documents(["text 1", "text 2"])  # code chunks, unit-length vectors
    vector = await provider.embed_query("where is auth handled?")    # a search question

To support another provider, subclass EmbeddingProvider and register it in get_provider().
"""

import asyncio
import math
import re
import time
from abc import ABC, abstractmethod

import httpx

from app import config

MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")  # Google duration format, e.g. "37s" or "0.5s"


class EmbeddingError(Exception):
    """An embedding failure that should be reported to the client.

    retryable: a later attempt may succeed (rate limit, provider outage, network).
    retry_after: seconds the provider asked us to wait, when known.
    """

    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool = False,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


class EmbeddingProvider(ABC):
    name: str
    model: str
    dimensions: int
    max_batch_size: int

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed texts that will be searched (code chunks). Returns one unit vector per text, in order."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a search question so it can be compared with embed_documents() vectors. Unit vector."""


def validate_vectors(raw, expected_count: int, dimensions: int) -> list[list[float]]:
    """Check a provider's output and L2-normalize it, so cosine similarity = dot product."""
    if not isinstance(raw, list) or len(raw) != expected_count:
        got = len(raw) if isinstance(raw, list) else "no"
        raise EmbeddingError(502, "embedding_bad_response",
                             f"The embedding provider returned {got} embeddings for {expected_count} inputs.",
                             retryable=True)
    vectors = []
    for values in raw:
        if not isinstance(values, list) or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            raise EmbeddingError(502, "embedding_bad_response", "The embedding provider returned a malformed vector.",
                                 retryable=True)
        if len(values) != dimensions:
            raise EmbeddingError(500, "embedding_dimension_mismatch",
                                 f"The model returned {len(values)}-dimensional vectors, but {dimensions} are expected "
                                 "(EMBEDDING_DIMENSIONS and the database column).")
        norm = math.sqrt(sum(v * v for v in values))
        if not math.isfinite(norm) or norm == 0:
            raise EmbeddingError(502, "embedding_bad_response", "The embedding provider returned an invalid (zero or "
                                 "non-finite) vector.", retryable=True)
        vectors.append([v / norm for v in values])
    return vectors


class GeminiEmbeddingProvider(EmbeddingProvider):
    """Google Gemini API (gemini-embedding-001) via REST: models/{model}:batchEmbedContents."""

    name = "gemini"
    max_batch_size = 100  # hard limit of batchEmbedContents

    def __init__(self, api_key: str, model: str, dimensions: int, base_url: str, timeout: float,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not MODEL_NAME_PATTERN.match(model):
            raise EmbeddingError(503, "embedding_not_configured", f"Invalid EMBEDDING_MODEL '{model}'.")
        self._api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self._base_url = base_url
        self._timeout = timeout
        self._transport = transport  # tests inject a mock transport

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        # Google's task type for natural-language questions about code; pairs with RETRIEVAL_DOCUMENT.
        return (await self._embed([text], "CODE_RETRIEVAL_QUERY"))[0]

    async def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        if not 1 <= len(texts) <= self.max_batch_size:
            raise ValueError(f"Gemini accepts 1-{self.max_batch_size} texts per request, got {len(texts)}.")
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                    "outputDimensionality": self.dimensions,
                }
                for text in texts
            ]
        }
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    f"/models/{self.model}:batchEmbedContents",
                    json=body,
                    headers={"x-goog-api-key": self._api_key},  # header, so the key never appears in URLs/logs
                )
        except httpx.TimeoutException:
            raise EmbeddingError(504, "embedding_timeout", "The embedding provider did not respond in time.", retryable=True)
        except httpx.RequestError:
            raise EmbeddingError(503, "embedding_unreachable", "Could not connect to the embedding provider.", retryable=True)

        if response.status_code >= 400:
            raise self._error(response)
        try:
            data = response.json()
        except ValueError:
            raise EmbeddingError(502, "embedding_bad_response", "The embedding provider returned invalid JSON.", retryable=True)

        embeddings = data.get("embeddings") if isinstance(data, dict) else None
        raw = [e.get("values") if isinstance(e, dict) else None for e in embeddings] if isinstance(embeddings, list) else None
        return validate_vectors(raw, len(texts), self.dimensions)

    def _error(self, response: httpx.Response) -> EmbeddingError:
        return google_api_error(response, self._api_key)


def google_api_error(response: httpx.Response, api_key: str, prefix: str = "embedding",
                     error_cls: type[EmbeddingError] = EmbeddingError) -> EmbeddingError:
    """Map a Google API error response to a client-facing error ({prefix}_auth_failed, ...)."""
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        error = {}
    message = str(error.get("message") or "")[:300].replace(api_key, "***")
    details = error.get("details") if isinstance(error.get("details"), list) else []
    reasons = {d.get("reason") for d in details if isinstance(d, dict)}
    status = response.status_code

    if "API_KEY_INVALID" in reasons or status == 401 or "API key not valid" in message:
        return error_cls(502, f"{prefix}_auth_failed", "Gemini rejected the API key. Check GEMINI_API_KEY in backend/.env.")
    if status == 403:
        return error_cls(502, f"{prefix}_auth_failed",
                         f"The API key is not allowed to use the Gemini API: {message or 'permission denied'}")
    if status == 429:
        daily = "PerDay" in response.text
        return error_cls(
            429, f"{prefix}_rate_limited",
            "Gemini daily quota exhausted; try again tomorrow or enable billing." if daily else "Gemini rate limit reached.",
            retryable=not daily,
            retry_after=None if daily else _retry_after(response, details),
        )
    if status >= 500:
        return error_cls(502, f"{prefix}_provider_error", f"Gemini error ({status}). Try again later.", retryable=True)
    return error_cls(502, f"{prefix}_request_rejected", f"Gemini rejected the request ({status}): {message}")


async def with_retries(call, deadline: float, max_retries: int):
    """Await call(), retrying retryable EmbeddingErrors (incl. subclasses) with backoff within a deadline."""
    attempt = 0
    while True:
        try:
            return await call()
        except EmbeddingError as exc:
            attempt += 1
            if not exc.retryable or attempt > max_retries:
                raise
            wait = exc.retry_after if exc.retry_after is not None else min(2 ** attempt, 30)
            if time.monotonic() + wait > deadline:
                raise  # longer than this call may take: report it, the client can try again later
            await asyncio.sleep(wait)


def _retry_after(response: httpx.Response, details: list) -> float | None:
    for detail in details:
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("google.rpc.RetryInfo"):
            match = _DURATION.match(str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    header = response.headers.get("retry-after")
    return float(header) if header and header.replace(".", "", 1).isdigit() else None


def provider_configured() -> bool:
    return config.EMBEDDING_PROVIDER == "gemini" and config.GEMINI_API_KEY is not None


def get_provider() -> EmbeddingProvider:
    """The configured provider. Raises a clear error when it cannot be used."""
    if config.EMBEDDING_PROVIDER != "gemini":
        raise EmbeddingError(503, "embedding_not_configured",
                             f"Unknown EMBEDDING_PROVIDER '{config.EMBEDDING_PROVIDER}'. Supported: gemini.")
    if not config.GEMINI_API_KEY:
        raise EmbeddingError(503, "embedding_not_configured",
                             "GEMINI_API_KEY is not set. Create a key at https://aistudio.google.com/apikey "
                             "and add it to backend/.env, then restart the backend.")
    return GeminiEmbeddingProvider(
        api_key=config.GEMINI_API_KEY,
        model=config.EMBEDDING_MODEL,
        dimensions=config.EMBEDDING_DIMENSIONS,
        base_url=config.GEMINI_API_URL,
        timeout=config.EMBEDDING_TIMEOUT_SECONDS,
    )
