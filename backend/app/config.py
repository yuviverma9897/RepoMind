"""Application settings loaded from environment variables.

Secrets and environment-specific values must come from the environment,
never be hardcoded here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env if present. Variables already set in the environment win.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

APP_NAME = "RepoMind API"
APP_VERSION = "0.12.0"
APP_ENV = os.getenv("APP_ENV", "development")

# Origins allowed by CORS. Defaults to the Vite dev server.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

# GitHub API
GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip() or None  # optional
GITHUB_TIMEOUT_SECONDS = float(os.getenv("GITHUB_TIMEOUT_SECONDS", "20"))

# Largest file (in bytes) whose contents are returned to the browser.
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_BYTES", str(500 * 1024)))

# ---------------------------------------------------------------- chunking
# Sizes are in characters (a dependency-free proxy for tokens: code averages
# roughly 3-4 characters per token). See README for the reasoning.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))

if CHUNK_SIZE < 200:
    raise ValueError("CHUNK_SIZE must be at least 200 characters.")
if not 0 <= CHUNK_OVERLAP < CHUNK_SIZE // 2:
    raise ValueError("CHUNK_OVERLAP must be >= 0 and less than half of CHUNK_SIZE.")

# Repository analysis limits (protect memory/CPU on huge repositories).
MAX_ARCHIVE_BYTES = int(os.getenv("MAX_ARCHIVE_BYTES", str(100 * 1024 * 1024)))  # compressed download
MAX_FILES_PER_ANALYSIS = int(os.getenv("MAX_FILES_PER_ANALYSIS", "3000"))
MAX_CHUNK_FILE_BYTES = int(os.getenv("MAX_CHUNK_FILE_BYTES", str(200 * 1024)))  # larger files are usually generated/data
ANALYSIS_CACHE_SIZE = int(os.getenv("ANALYSIS_CACHE_SIZE", "2"))
ANALYSIS_CACHE_TTL_SECONDS = int(os.getenv("ANALYSIS_CACHE_TTL_SECONDS", "600"))


# ---------------------------------------------------------------- Supabase & database
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://gfkunutisfqnaggampxm.supabase.co").strip()
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_DB_HOST = os.getenv("SUPABASE_DB_HOST", "aws-0-ap-southeast-1.pooler.supabase.com").strip()
SUPABASE_DB_PORT = os.getenv("SUPABASE_DB_PORT", "6543").strip()
SUPABASE_DB_USER = os.getenv("SUPABASE_DB_USER", "postgres.gfkunutisfqnaggampxm").strip()
SUPABASE_DB_PASSWORD = os.getenv("SUPABASE_DB_PASSWORD", "").strip()


def _sqlalchemy_url(url: str | None) -> str | None:
    """Accept postgresql:// and sqlite:// forms and select the proper driver."""
    if not url:
        # If SUPABASE credentials provided with password, build the pooler URL
        if SUPABASE_DB_PASSWORD and SUPABASE_DB_HOST:
            import urllib.parse
            encoded_pw = urllib.parse.quote_plus(SUPABASE_DB_PASSWORD)
            return f"postgresql+psycopg://{SUPABASE_DB_USER}:{encoded_pw}@{SUPABASE_DB_HOST}:{SUPABASE_DB_PORT}/postgres"
        return "sqlite:///./repomind.db"
    if ":@" in url and not SUPABASE_DB_PASSWORD:
        return "sqlite:///./repomind.db"
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


# e.g. postgresql://user:password@localhost:5432/repomind or sqlite:///repomind.db
DATABASE_URL = _sqlalchemy_url(os.getenv("DATABASE_URL", "").strip())
DATABASE_ECHO = os.getenv("DATABASE_ECHO", "false").lower() == "true"  # log SQL statements



# ---------------------------------------------------------------- embeddings (Phase 6)
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini").strip().lower()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001").strip()
# Must equal the size of the code_chunks.embedding column (vector(768), migration 0002).
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))
# Chunks per provider request. Gemini accepts at most 100; 20 stays safely under rate limits.
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "20"))
# Each POST /embeddings call works for about this long, then reports progress
# (the client calls again to continue). Keeps HTTP requests short.
EMBEDDING_REQUEST_BUDGET_SECONDS = float(os.getenv("EMBEDDING_REQUEST_BUDGET_SECONDS", "15"))
EMBEDDING_MAX_RETRIES = int(os.getenv("EMBEDDING_MAX_RETRIES", "3"))  # per batch, for transient errors
EMBEDDING_TIMEOUT_SECONDS = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "60"))

# Google Gemini API key (https://aistudio.google.com/apikey). Backend only: never
# sent to the browser or included in responses or logs.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip() or None
GEMINI_API_URL = os.getenv("GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

if not 1 <= EMBEDDING_BATCH_SIZE <= 100:
    raise ValueError("EMBEDDING_BATCH_SIZE must be between 1 and 100 (Gemini's per-request limit).")


# ---------------------------------------------------------------- LLM answers / RAG (Phase 8)
# Uses the same GEMINI_API_KEY / GEMINI_API_URL as the embeddings.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.5-flash").strip()
# Thinking level
LLM_THINKING_LEVEL = os.getenv("LLM_THINKING_LEVEL", "").strip().lower()
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4096"))  # includes thinking tokens
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))  # transient errors only (overload, network)
LLM_REQUEST_BUDGET_SECONDS = float(os.getenv("LLM_REQUEST_BUDGET_SECONDS", "90"))
# Upper bound for the code sent to the LLM per question (~4 characters per token).
RAG_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "24000"))

if LLM_THINKING_LEVEL not in {"", "low", "medium", "high"}:
    raise ValueError("LLM_THINKING_LEVEL must be low, medium or high (or empty for the model default).")

# ---------------------------------------------------------------- evidence (Phase 9)
# Below this best-source cosine similarity an answer's evidence is reported as "weak".
# Provisional: calibrate against real gemini-embedding-001 scores in the final Gemini test.
RAG_WEAK_SIMILARITY = float(os.getenv("RAG_WEAK_SIMILARITY", "0.5"))
if not 0 <= RAG_WEAK_SIMILARITY <= 1:
    raise ValueError("RAG_WEAK_SIMILARITY must be between 0 and 1.")
