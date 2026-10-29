# RepoMind

AI-Powered GitHub Repository Intelligence & Multi-Agent Code Analysis Platform.

> Status: **Phase 12: debug & security analysis (v0.12.0).** Import a public GitHub repository, browse
> its files, chunk and store the code (PostgreSQL + pgvector), search it by meaning, ask questions
> answered from cited sources, explain code, explore the dependency graph and change impact, and run
> debug / security investigations that produce *potential* findings for human review.
>
> **Real Gemini generation has not been tested yet**: every AI feature is tested with stub models.
> See [Final Gemini integration test](#final-gemini-integration-test).

## Structure

```
RepoMind/
├── frontend/            React + Vite + Tailwind CSS
├── backend/             Python + FastAPI + SQLAlchemy + Alembic
└── docker-compose.yml   optional: PostgreSQL 17 + pgvector for development
```

Backend layout:

```
backend/app/
├── main.py              app setup, CORS, error handling, health
├── config.py            settings from environment variables
├── api/repos.py         /api/repos/* routes
├── models/              request/response shapes (Pydantic)
├── db/
│   ├── database.py      engine, sessions, connection status
│   ├── models.py        tables: repositories, code_chunks (+ embedding vector(768))
│   └── store.py         all SQL queries
└── services/
    ├── github.py        GitHub REST API (import, file, archive download)
    ├── file_types.py    language detection, binary detection
    ├── file_filter.py   which files are worth processing
    ├── parsers/         python (ast), brace-heuristic (JS/TS/Java/C/C++/C#/Go/Rust), markdown
    ├── chunker.py       units -> size-bounded chunks with exact line ranges
    ├── pipeline.py      archive -> filter -> parse -> chunk -> summary
    ├── indexing.py      analyze & index: GitHub + pipeline -> database (sync logic)
    ├── embedding_service.py  chunks -> embedding text -> provider -> pgvector (batches, retries, resume)
    ├── search.py        semantic search: question -> query embedding -> pgvector nearest chunks
    ├── embeddings.py    embedding provider interface + Gemini implementation
    ├── llm.py           text-generation provider interface + Gemini (masks secrets in every prompt/answer)
    ├── rag.py           retrieval -> numbered sources [S1].. -> grounded answer (shared by all AI features)
    ├── citations.py     citation validation + evidence level
    ├── repo_code.py     rebuild stored files from chunks, symbols
    ├── code_intelligence.py  explain files / functions / classes / selections
    ├── dependencies.py  import extraction + resolution, graph views, deterministic impact
    ├── architecture.py  AI architecture summary + AI impact assessment
    ├── secrets.py       secret detection and length-preserving masking
    ├── findings.py      shared finding shape + validation of AI-proposed findings
    ├── security.py      security rules + optional AI review
    └── debug.py         debug investigation (retrieval + rule hints + AI causes)
backend/alembic/         database migrations (0001 initial schema, 0002 embedding columns)
```

Request flow: `api/repos.py` -> `services/indexing.py` -> (`services/github.py` + `services/pipeline.py`)
-> `db/store.py` -> PostgreSQL. Routes contain no SQL; GitHub/chunking code knows nothing about the database.

## Database setup (PostgreSQL + pgvector)

RepoMind needs PostgreSQL 13+ with the [pgvector](https://github.com/pgvector/pgvector) extension
available. Without a database, import / file viewer / chunk preview still work; only indexing does not.

**Option A: Docker** (any OS; easiest)

```bash
# from the repository root
POSTGRES_PASSWORD=choose-a-password docker compose up -d
# backend/.env:
# DATABASE_URL=postgresql://repomind:choose-a-password@localhost:5432/repomind
```

The container user is a superuser, so the migration enables pgvector by itself.

**Option B: native install** (Debian/Ubuntu shown; on macOS: `brew install postgresql@17 pgvector`)

```bash
sudo apt install postgresql postgresql-17-pgvector     # match your PostgreSQL major version
sudo -u postgres psql <<'SQL'
CREATE ROLE repomind LOGIN PASSWORD 'choose-a-password';
CREATE DATABASE repomind OWNER repomind;
CREATE DATABASE repomind_test OWNER repomind;   -- only needed for the test suite
SQL
# Enable pgvector (needs a superuser, once per database):
sudo -u postgres psql -d repomind      -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d repomind_test -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Then put `DATABASE_URL` (and optionally `TEST_DATABASE_URL`) in `backend/.env`; see `.env.example`.

### Migrations (Alembic)

The schema is created only by migrations; the app never creates or drops tables on its own.

```bash
cd backend
alembic upgrade head            # create / update the schema (safe to re-run)
alembic current                 # show the applied revision
alembic upgrade head --sql      # print the SQL without running it
```

The initial migration also runs `CREATE EXTENSION IF NOT EXISTS vector`. If pgvector is not
installed, or the database user may not create extensions, it logs a warning and still creates
the tables; `GET /api/health` then shows `"pgvector": null`. Fix it by running the
`CREATE EXTENSION` command above as a superuser.

After changing `app/db/models.py`, create a migration and **review it** before applying:

```bash
alembic revision --autogenerate -m "describe the change"
```

### Resetting the development database

These commands **delete all stored repositories and chunks**. Nothing in the app does this automatically.

```bash
alembic downgrade base && alembic upgrade head       # drop and recreate RepoMind's tables
# or start completely fresh:
sudo -u postgres dropdb repomind && sudo -u postgres createdb -O repomind repomind
sudo -u postgres psql -d repomind -c "CREATE EXTENSION vector;" && alembic upgrade head
# Docker: docker compose down -v   (removes the data volume)
```

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

Database tests run against `TEST_DATABASE_URL` (a separate database: the tests wipe it and
re-run the migrations). They are skipped if it is not set and refuse to run if it equals `DATABASE_URL`.

## Run the backend

```bash
cd backend
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env            # then edit DATABASE_URL (and optionally GITHUB_TOKEN)
alembic upgrade head
uvicorn app.main:app --reload --reload-dir app --port 8000
```

`backend/.env` is loaded automatically. A `GITHUB_TOKEN` raises the GitHub rate limit from
60 to 5,000 requests/hour.

- API root: http://localhost:8000/
- Health:   http://localhost:8000/api/health  (includes database / migration / pgvector status)
- Docs:     http://localhost:8000/docs

## Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 and try `https://github.com/octocat/Hello-World`.

## API

### `POST /api/repos/import`

Request: `{ "url": "https://github.com/owner/repository" }`

Success (200):

```json
{
  "repository": { "owner": "...", "name": "...", "full_name": "...", "description": "...",
                  "html_url": "...", "stars": 0, "forks": 0, "language": "...", "default_branch": "..." },
  "files": [ { "path": "src", "type": "directory" }, { "path": "src/App.jsx", "type": "file" } ],
  "tree":  { "truncated": false, "empty": false, "file_count": 1, "directory_count": 1 }
}
```

Errors: `{ "detail": "<human-readable message>", "code": "<machine code>" }`

| HTTP | code | When |
|---|---|---|
| 400 | `invalid_url` / `missing_owner_or_repo` | Bad or incomplete URL |
| 403 | `private_repo` / `access_denied` | Private or forbidden repository |
| 404 | `repo_not_found` | Doesn't exist (or private: GitHub hides those) |
| 422 | (FastAPI validation) | Malformed request body |
| 429 | `rate_limited` | GitHub rate limit hit |
| 502 | `github_error` / `github_auth_failed` | GitHub error or invalid token |
| 503 / 504 | `github_unreachable` / `github_timeout` | Network failure / timeout |

An empty repository returns 200 with `files: []` and `tree.empty: true`.

### `GET /api/repos/file`

Query parameters: `owner`, `repo`, `branch`, `path` (all required).

```
GET /api/repos/file?owner=react&repo=react&branch=main&path=packages/react/index.js
```

Success (200):

```json
{
  "path": "packages/react/index.js", "name": "index.js", "language": "javascript",
  "size": 1835, "kind": "text", "content": "...source...", "message": null,
  "html_url": "https://github.com/react/react/blob/main/packages/react/index.js"
}
```

`kind` is `"text"`, `"binary"` or `"too_large"`. For the last two, `content` is `null` and
`message` explains why. The size limit is `MAX_FILE_SIZE_BYTES` (default 500 KB).

Additional errors: `400 invalid_repository | invalid_branch | invalid_path | not_a_file`,
`404 file_not_found`, `502 decode_failed`, plus the GitHub/network errors listed above.

### `POST /api/repos/chunks`

Request: `{ "owner": "...", "repo": "...", "branch": "...", "offset": 0, "limit": 50,
"file_path": null, "include_files": true }` (`limit` max 200).

Downloads the repository once as a tarball (1 GitHub API request), filters files, parses them,
and returns a summary plus **one page** of chunks. The analysis is cached briefly
(`ANALYSIS_CACHE_TTL_SECONDS`), so further pages and file filters are instant.

Each chunk: `chunk_id, repository, file_path, language, chunk_type, symbol_name, parent_symbol,
start_line, end_line, part, total_parts, content`.

### Chunk size defaults

Sizes are measured in characters (no tokenizer dependency; code averages ~3-4 chars/token).

- `CHUNK_SIZE=1500` (~400-500 tokens, ~40-50 lines): about one typical function; fits within
  the 512-token input limit of many embedding models; small enough for precise citations.
- `CHUNK_OVERLAP=150` (~10%, a few lines): only used when one symbol must be split, so each
  part keeps some context from the previous one.

### `POST /api/repos/analyze`: analyze & index

Request: `{ "owner": "pallets", "repo": "flask", "branch": "main" }` (`branch` optional: default branch).

Fetches metadata (1 GitHub request) and the current snapshot (1 request), runs the Phase 4
pipeline and stores the chunks. Response (abridged):

```json
{ "repository_id": 3, "repository": "pallets/flask", "status": "indexed",
  "commit_sha": "d73fa1c...", "files_processed": 110, "chunks_created": 1131, "chunks_total": 1131,
  "languages": ["python", "html", "markdown", "css", "json"],
  "files_added": 110, "files_updated": 0, "files_removed": 0, "files_unchanged": 0, "duration_ms": 1054 }
```

**Re-analysis does not duplicate data.** Every stored chunk records the sha256 of its file.
On the next run, files with the same hash are left untouched, changed files have their chunks
replaced, removed files are deleted and new files are added, all in one transaction. If the
chunker version or `CHUNK_SIZE`/`CHUNK_OVERLAP` changed, everything is rebuilt. A unique
constraint on `(repository_id, chunk_key)` guards against duplicates at the database level.
An unchanged repository returns `"status": "unchanged"` with `chunks_created: 0`.

### `GET /api/repos/index-status?owner=&repo=`

`{ "indexed": true, "repository_id": 3, "indexed_at": "...", "commit_sha": "...", "chunks_total": 1131, ... }`
(database only, no GitHub request).

### `GET /api/repos/{repository_id}/chunks?page=1&page_size=20&file_path=`

Stored chunks ordered by file and line; `page_size` max 100.
Returns `{ repository_id, repository, total, page, page_size, total_pages, chunks: [...] }`.

## Code embeddings (Phase 6)

```
POST /embeddings -> embedding_service (batching, retries, resume) -> embeddings.py provider (Gemini HTTP) -> PostgreSQL / pgvector
```

**Model:** Google `gemini-embedding-001`, stored at **768 dimensions** (`vector(768)`). The model
returns 3072 by default; we ask for 768 (`outputDimensionality`), which Google recommends and which
scores almost the same on MTEB (67.99 vs 68.17) at a quarter of the storage. Below 3072 the
vectors are not unit length, so the backend L2-normalises them (cosine similarity = dot product).
It also checks every response really has 768 numbers before storing anything.

**Setup:** create a key at <https://aistudio.google.com/apikey>, put it in `backend/.env` as
`GEMINI_API_KEY=...`, restart the backend. The key stays in the backend. It is never sent to the
browser or returned by any endpoint (`/api/health` only reports `api_key_configured: true/false`).

**Text sent per chunk:** a short header, then the code:

```
File: src/auth/session.py
Language: python
Symbol: method SessionStore.refresh     (or "Type: imports" for chunks without a symbol)
Part 2 of 3                             (only for large symbols that were split)

def refresh(self):
    ...
```

Line numbers, hashes and ids are left out because they carry no meaning. Whitespace-only chunks
are not sent; they count as `skipped`.

### `POST /api/repos/{repository_id}/embeddings`

The operation is **synchronous** and works in steps, with no background worker. One call embeds
pending chunks in batches of `EMBEDDING_BATCH_SIZE` (one provider request per batch, max 100) for
about `EMBEDDING_REQUEST_BUDGET_SECONDS`. It then returns progress. Call it again until
`status` is `complete`; the UI does this automatically.

- Only chunks without an embedding from the current model are processed, so a second call does not regenerate anything.
- Each batch is committed on its own. If a batch fails, earlier batches stay saved and the next call resumes.
- Rate limits, 5xx errors and network errors are retried (honouring Google's `retryDelay`) up to `EMBEDDING_MAX_RETRIES`.

Response: `repository_id, provider, model, dimensions, total_chunks, embedded_chunks,
skipped_chunks, failed_chunks, remaining_chunks, progress_percent, newly_embedded, already_embedded,
status (complete | in_progress | interrupted), error {code, message, retry_after_seconds}, batches, duration_ms`.

Errors (`{detail, code}`) when nothing could be done:

| Code | Status | Meaning |
|---|---|---|
| `embedding_not_configured` | 503 | `GEMINI_API_KEY` is missing |
| `embedding_auth_failed` | 502 | key rejected or not allowed |
| `embedding_rate_limited` | 429 | includes `retry_after_seconds` (not set for the daily quota) |
| `embedding_provider_error`, `embedding_timeout`, `embedding_unreachable` | 502/504/503 | provider or network problem |
| `embedding_bad_response` | 502 | malformed response |
| `embedding_dimension_mismatch` | 500 | config/model vs `vector(768)` column |
| `embedding_in_progress` | 409 | already running for this repo |
| `repository_not_indexed` | 404 | unknown id |

### `GET /api/repos/{repository_id}/embedding-status`

`repository_id, provider, model, dimensions, provider_configured, total_chunks, embedded_chunks,
skipped_chunks, remaining_chunks, outdated_chunks, progress_percent, last_embedded_at`.

### Cost and limits

Paid tier: $0.15 per 1M input tokens. A chunk is about 400 tokens, so 1,000 chunks cost about
$0.06. The **free tier** allows about 100 requests/min, 30k tokens/min and **1,000 requests/day**
(check your limits in AI Studio). The token-per-minute limit is usually hit first. The backend
waits and retries automatically; if the daily quota runs out, progress is kept and you resume
the next day.

### Changing the model or dimension

- **Another model with the same size:** set `EMBEDDING_MODEL`. Chunks embedded by the old model
  show up as `outdated_chunks` and are re-embedded on the next run (vectors from different models
  must never be mixed).
- **Another size** (e.g. 1536): add an Alembic migration that changes the column to
  `vector(1536)` (clearing old vectors), then set `EMBEDDING_DIMENSIONS=1536`. The backend
  refuses to run if config and column disagree.
- **Another provider:** implement `EmbeddingProvider` in `app/services/embeddings.py` and
  register it in `get_provider()`. Nothing else changes.

## Semantic code search (Phase 7)

```
POST /search -> services/search.py -> embedding_service.embed_query -> provider (Gemini)
                                   -> db/store.search_chunks -> PostgreSQL / pgvector
```

The question is embedded with the same model as the chunks, using Gemini's `CODE_RETRIEVAL_QUERY`
task type, which Google pairs with `RETRIEVAL_DOCUMENT` for code search. PostgreSQL then returns
the nearest chunks. **No LLM writes anything:** results are the stored chunks themselves, with
their file, symbol, line range and similarity.

**Metric: cosine similarity** (`similarity = 1 - (embedding <=> query)`). Gemini embeddings are
meant to be compared by cosine. Stored vectors already have length 1, so cosine and dot product
rank identically, and cosine gives an easy-to-read score (1 = same direction).

**Query** (exact search; repository isolation is in the SQL itself):

```sql
SELECT id, file_path, language, chunk_type, symbol_name, ..., 1 - (embedding <=> :q) AS similarity
FROM code_chunks
WHERE repository_id = :repository_id
  AND embedding_model = :current_model      -- never compare vectors from different models
  AND embedding IS NOT NULL
ORDER BY embedding <=> :q
LIMIT :top_k
```

### `POST /api/repos/{repository_id}/search`

Request: `{ "query": "Where is user authentication handled?", "top_k": 8 }`. `top_k` is 1-50
(default 8), and the query is 1-1000 characters after trimming.

Response: `{ repository_id, query, top_k, model, metric, total_chunks, searched_chunks, partial,
message, took_ms, results: [{ rank, chunk_id, repository_id, file_path, language, chunk_type,
symbol_name, parent_symbol, start_line, end_line, part, total_parts, similarity, content }] }`.

| Code | Status | When |
|---|---|---|
| `empty_query`, `query_too_long`, `invalid_top_k` | 400 | invalid request (the provider is not called) |
| `repository_not_indexed` | 404 | unknown repository id |
| `repository_has_no_chunks` | 409 | analyze & index first |
| `embeddings_not_ready` | 409 | "Repository embeddings are not ready. Generate embeddings before searching." |
| `embedding_*` | 429/502/503 | query embedding failed (missing/invalid key, rate limit, network) |
| `database_unavailable` | 503 | PostgreSQL unreachable |

If only some chunks are embedded, the search still runs, but `partial: true` and `message` say so.
Similarity is relative: every search returns its nearest chunks. A weak best score (see the
examples in the Phase 7 report) means nothing really matched.

### Vector index: why there is none (yet)

Search is an **exact** scan of one repository's vectors, so it always returns the true top-k.
PostgreSQL finds the repository's rows through the existing `(repository_id, …)` indexes.
Measured on this dev machine (2 CPUs, 768-dim vectors, top_k=8):

| Repository | Chunks | Exact search |
|---|---|---|
| flask | 1,131 | ~10 ms |
| gin | 2,116 | ~16 ms |
| react | 23,255 | ~150 ms |

An approximate **HNSW** index (`USING hnsw (embedding vector_cosine_ops)`) was tried on the same
data (104 MB, 12 s to build). It cut react to ~8 ms, but:

- pgvector applies `WHERE repository_id = …` **after** the index scan, so with one shared index a
  small repository got back as few as **1 of 8** results. `SET hnsw.iterative_scan =
  relaxed_order` (pgvector ≥ 0.8) fixed the count.
- Approximate results differ from the exact top-k and need tuning (`hnsw.ef_search`) and recall
  checks on real embeddings.

So the index is left out for now: correctness first, and exact search is fast enough at this
size. Consider HNSW once single repositories reach roughly 50k-100k embedded chunks or search
becomes too slow. Use `vector_cosine_ops` with iterative scans, check recall against exact search,
and keep `ORDER BY embedding <=> :q LIMIT k` (already the query shape) so the index can be used.

## Grounded answers / RAG (Phase 8)

The **Repository Copilot** panel answers questions about an embedded repository from its own
code. Each question is one backend request:

1. **Retrieve**: the question is embedded and searched exactly like `/search` (same service,
   same repository isolation). The default is the top 8 chunks; the maximum is 20.
2. **Build context**: the chunks are labelled `[S1]`, `[S2]`, … with path, lines, language and
   symbol. Each chunk is capped at 4,000 characters and the total at `RAG_MAX_CONTEXT_CHARS`
   (24,000 ≈ 6k tokens). Sources that don't fit are dropped and are **not** returned, so the
   listed sources are exactly what the model saw. The whole repository is never sent.
3. **Generate**: `services/llm.py` calls Gemini `generateContent` (`LLM_MODEL`, default
   `gemini-3.8-flash`, `thinkingLevel=low`). The system instruction tells the model to use
   only the sources, cite them as `[S#]`, say when the evidence is insufficient, and ignore any
   instructions found inside the code.

The frontend never talks to Gemini; the key stays in `backend/.env`. Nothing is stored: the
Q&A list exists only in the browser tab. Clicking a source opens it in the code viewer with
its lines highlighted.

**Limits.** Answers can be wrong or incomplete when retrieval misses the relevant code, so always
check the sources. Citations are validated since Phase 9 (see below).

### `POST /api/repos/{repository_id}/ask`

```json
{ "question": "How does login work?", "top_k": 8 }
```

Response: `answer` (Markdown text), `sources` (each search result plus `source_id`), `model`,
`finish_reason`, `truncated` (true when the answer hit `LLM_MAX_OUTPUT_TOKENS`), `usage`
(`input_tokens`, `output_tokens`, `thinking_tokens`), `partial` and `message` (when only part
of the repository is embedded), `retrieval_ms` and `took_ms`.

| Status | `code` | Meaning |
|---|---|---|
| 400 | `empty_query`, `query_too_long`, `invalid_top_k` | Bad request (no API call made) |
| 404 | `repository_not_indexed` | Unknown repository id |
| 409 | `embeddings_not_ready` | Generate embeddings first (the LLM is not called) |
| 503 | `llm_not_configured` | `GEMINI_API_KEY` missing |
| 429 | `llm_rate_limited` | Gemini quota hit; `retry_after_seconds` when known |
| 502 | `llm_auth_failed`, `llm_blocked`, `llm_output_limit`, `llm_request_rejected`, `llm_provider_error`, `llm_bad_response` | Provider problem |
| 503 / 504 | `llm_unreachable` / `llm_timeout` | Network problems |

**Cost.** One question = one embedding call + one `generateContent` call. With the defaults
that is ≈ 3–7k input tokens and up to 4,096 output tokens (thinking included). See Google's
pricing page for current rates; `gemini-3.8-flash` has a free tier with per-project limits.

## Evidence and citations (Phase 9)

Every AI answer is generated from numbered sources. Each source carries `source_id` (S1, S2, ...),
`chunk_id`, `repository_id`, `file_path`, `language`, `symbol_name`, `chunk_type`, `start_line`,
`end_line` and `similarity`. The backend then checks the answer (`services/citations.py`):

- `[S1]`, `[S1, S3]` and `[s1; S3]` are normalised to `[S1][S3]`; ids that match no source (e.g. `[S99]`)
  are removed and listed in `citations.invalid`.
- `evidence.level`: `supported`, `weak` (best similarity below `RAG_WEAK_SIMILARITY`), `uncited` (no valid
  citation) or `none` (no sources). Weak or uncited answers get a warning in the UI.
- `sources[].cited` marks the sources the answer actually uses. Clicking a source or a citation chip opens
  the code viewer at those lines.

## Code intelligence (Phase 10)

- `GET /api/repos/{id}/symbols?file_path=`: functions, methods and classes of a stored file.
- `POST /api/repos/{id}/explain` `{file_path, symbol?, parent?, start_line?, end_line?}`: explains a whole
  file, one symbol or a line selection. Static facts (parameters, return type, calls, raises) come from Python's
  `ast` or a brace heuristic (JS/TS/Go/Java...), and are sent with the code and related definitions as sources.
  Unsupported structures are explained without facts (the response says so). Invalid selections return 400.
- "Ask about this code" sends `/ask` with a `focus` (the open file/selection pinned as `[S1]`).

UI: **Files** section → Explain Code / Explain Function / Ask About This Code.

## Architecture, dependencies and impact (Phase 11)

`services/dependencies.py` extracts imports with regular expressions from Python, JavaScript/TypeScript
and Go, then resolves them against the repository's files (relative paths, Python packages, `src/` roots,
JS index files, Go modules from `go.mod` or inferred). Every import is `resolved`, `inferred`, `external`
or `unresolved`; other languages are reported as **unsupported**, not silently skipped. Graphs are cached
until the repository is re-indexed.

- `GET /api/repos/{id}/graph?level=directory|file&depth=1-6&path_prefix=&max_nodes=1-300&include_external=`
  returns `{nodes, edges, total_nodes, truncated, stats, limitations}`. Nodes have `id`, `label`, `type`
  (`file`, `directory`, `package`, `external`), `file` (path for file nodes), `language` and degrees; edges have
  `source`, `target`, `type` (`imports`) and `weight` (number of import statements). At most `max_nodes` (default 80)
  of the most connected nodes are returned, so the browser never draws thousands of nodes.
- `POST /api/repos/{id}/architecture/summary {path_prefix?}`: AI overview grounded in graph facts, the README,
  hub files and entry points.
- `POST /api/repos/{id}/impact {file_path, symbol?, depth 1-3}`: deterministic: direct and indirect importers,
  name references to the symbol (flagged when the file does not import the target), and the file's own imports.
- `POST /api/repos/{id}/impact/explain`: the same facts plus an AI assessment of what may break and why.

The Architecture screen is a small hand-written SVG (layered layout, hover highlighting, click for details).
React Flow was not added because a bounded, read-only graph did not need it. **Impact analysis is never
guaranteed to be complete** (dynamic imports, reflection, string-based lookups and unsupported languages are invisible).

## Debug and security analysis (Phase 12)

Both produce **potential findings** with `title`, `category`, `severity`, `confidence`, `file_path`,
`start_line`, `end_line`, `snippet`, `explanation`, `reasoning`, `recommendation`, `requires_review` (always
true), `origin` (`rule` or `ai`), `rule_id` and `non_production` (tests, examples, docs, build/CI scripts:
severity lowered one level). RepoMind never modifies code.

- `POST /api/repos/{id}/security {path_prefix?, use_ai=false}`
  - Rules (Python, JS/TS, Go): shell commands, `eval`/`exec`, SQL built from strings, unsafe deserialization,
    disabled TLS checks, debug mode, CORS `*`, XSS sinks, path from request data, weak hashes, JWT without
    signature check, hard-coded password comparisons. Comments and string literals are skipped.
  - Secret detection for all text files: AWS/GitHub/Google/Slack/Stripe/`sk-` keys, JWTs, private keys,
    passwords in URLs and `password = "..."`-style assignments (placeholders, words-only values and
    low-entropy values are ignored). Other languages get secret detection only (`secrets_only_languages`).
  - `use_ai=true` adds **one** LLM call: it rates up to 8 rule findings (`likely_issue`,
    `likely_false_positive`, `uncertain`) and may propose new findings in authentication / input-handling code.
  - At most 200 findings are returned (most severe first, `truncated` when cut). The rule scan is cached until re-indexing.
- `POST /api/repos/{id}/debug {description?, file_path?, symbol?, parent?, start_line?, end_line?, top_k ≤12, use_ai=true}`
  (description ≤1000 chars; optional when a file or function is selected, e.g. Files → **Bugs** / *Investigate potential bugs*):
  semantic search + optional pinned file → rule hints on that code (bare/swallowed exceptions, mutable defaults,
  empty catch, async `forEach`, discarded Go errors, TODO/FIXME) → one LLM call returning a cited summary and
  potential causes. `use_ai=false` works without an API key.
- **AI findings are validated**: they must name a source the model was given and lines inside it, with a known
  category and severity; anything else is dropped and counted (`rejected`). Citations in the summary are
  validated like `/ask`.
- **Secret masking** (`services/secrets.py`): detected values are replaced with `*` (same length, so line numbers
  stay correct) in every LLM prompt and answer (`llm.generate`), sources, findings, search results, stored and
  live chunks, impact references and the file viewer (which shows a notice). The database keeps the original code.

UI: **Debug** and **Security** sections with severity/category/origin filters and a "hide tests / examples /
tooling" toggle; clicking a location opens the code viewer.

**Limitations.** Pattern rules miss logic flaws and can flag safe code; the AI can be wrong in both directions;
secrets in unusual formats (or a private key split across chunks) can be missed. Treat results as leads for review,
not as an audit.

## Final Gemini integration test

All automated tests use stub models; real Gemini generation is **not yet tested**. To run it:

1. In `backend/.env` set `GEMINI_API_KEY=<your key>` (see `.env.example` for the optional `LLM_*` settings).
2. Restart the backend (`uvicorn app.main:app --reload --reload-dir app`).
3. Index a small repository (e.g. `pallets/flask`), generate embeddings (Overview), and re-run embeddings for any
   repository embedded earlier with a different model.
4. Check, in order: Search, Copilot (`How does the tutorial app log users in and protect views?`; expect
   citations to `flaskr/auth.py` `login_required` / `load_logged_in_user`), Explain Function on `get_db`,
   Architecture summary, Impact → Explain with AI on `flaskr/db.py`, Debug → Investigate, Security → Scan + AI review.
5. For each: citations open the right lines, no invalid-citation flood, no raw secrets, and errors such as 429
   show a readable message.

## Database schema

`repositories`: `id`, `github_id` (unique; GitHub's id survives renames), `owner`, `name`,
`full_name`, `github_url`, `default_branch`, `description`, `language`, `stars`, `forks`,
`created_at`, `updated_at`, plus index state: `indexed_at`, `indexed_branch`,
`indexed_commit_sha`, `index_config`, `index_stats` (JSONB).

`code_chunks`: `id`, `repository_id` (FK, `ON DELETE CASCADE`), `chunk_key`, `file_path`,
`file_hash`, `language`, `chunk_type`, `symbol_name`, `parent_symbol`, `start_line`, `end_line`,
`part`, `total_parts`, `content`, `created_at`, `updated_at`.
Unique `(repository_id, chunk_key)`; index `(repository_id, file_path, start_line)`; check `1 <= start_line <= end_line`.

Phase 6 adds `embedding vector(768)`, `embedding_model` and `embedded_at` (migration `0002`).
Unchanged files keep their rows on re-index, so their embeddings are kept too.

## Configuration

Configuration comes from environment variables. See `backend/.env.example` and
`frontend/.env.example`. Never commit `.env` files or API keys.
