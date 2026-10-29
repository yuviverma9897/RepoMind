from datetime import datetime, timezone

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError

from app.api.repos import router as repos_router
from app.db import database
from app.db.database import DatabaseError
from app.services.embeddings import EmbeddingError, provider_configured
from app.services.llm import llm_configured, LLMError
from app.agents import orchestrator
from app.models.agents import AgentInfo
from app.services.github import GitHubError
from app.services.search import SearchError

log = logging.getLogger(__name__)

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(GitHubError)
async def github_error_handler(_: Request, exc: GitHubError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
    )


@app.exception_handler(DatabaseError)
async def database_error_handler(_: Request, exc: DatabaseError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(EmbeddingError)
async def embedding_error_handler(_: Request, exc: EmbeddingError):
    content = {"detail": exc.message, "code": exc.code}
    headers = None
    if exc.retry_after is not None:
        content["retry_after_seconds"] = exc.retry_after
        headers = {"Retry-After": str(max(1, round(exc.retry_after)))}
    return JSONResponse(status_code=exc.status_code, content=content, headers=headers)


@app.exception_handler(LLMError)
async def llm_error_handler(_: Request, exc: LLMError):
    content = {"detail": exc.message, "code": exc.code}
    headers = None
    if exc.retry_after is not None:
        content["retry_after_seconds"] = exc.retry_after
        headers = {"Retry-After": str(max(1, round(exc.retry_after)))}
    return JSONResponse(status_code=exc.status_code, content=content, headers=headers)


@app.exception_handler(SearchError)
async def search_error_handler(_: Request, exc: SearchError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_error_handler(_: Request, exc: SQLAlchemyError):
    if isinstance(exc, OperationalError):
        status, code, message = 503, "database_unavailable", "Could not connect to the database. Is PostgreSQL running?"
    elif isinstance(exc, ProgrammingError) and "does not exist" in str(exc.orig):
        status, code, message = 503, "database_not_migrated", "Database tables are missing. Run: alembic upgrade head"
    else:
        log.exception("Database error")
        status, code, message = 500, "database_error", "An unexpected database error occurred."
    return JSONResponse(status_code=status, content={"detail": message, "code": code})


@app.get("/")
def root():
    return {
        "name": config.APP_NAME,
        "version": config.APP_VERSION,
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/agents", response_model=list[AgentInfo])
def list_agents():
    """The specialist agents the orchestrator can route to."""
    return orchestrator.catalog()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "repomind-backend",
        "version": config.APP_VERSION,
        "environment": config.APP_ENV,
        "github_token_configured": config.GITHUB_TOKEN is not None,
        "database": database.status(),
        "embeddings": {
            "provider": config.EMBEDDING_PROVIDER,
            "model": config.EMBEDDING_MODEL,
            "dimensions": config.EMBEDDING_DIMENSIONS,
            "api_key_configured": provider_configured(),  # never the key itself
        },
        "llm": {
            "provider": config.LLM_PROVIDER,
            "model": config.LLM_MODEL,
            "thinking_level": config.LLM_THINKING_LEVEL,
            "api_key_configured": llm_configured(),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


app.include_router(repos_router)
