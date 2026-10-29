"""Rule-based orchestrator: picks one specialist agent per request, runs it and records real activity.

Routing is deterministic (keyword rules + file/symbol mentions resolved against the indexed files), so every
decision can be explained: the response says which rules matched and why an agent was chosen. Anything that does
not match a specialist goes to the research agent (semantic search + grounded answer)."""

import asyncio
import logging
import posixpath
import re
import time

from app.agents.base import SERVICE_ERRORS, Activity, error_info, result
from app.agents.specialists import AGENTS
from app.db import database, store
from app.services import findings as F, repo_code

log = logging.getLogger(__name__)
MAX_QUERY = 2000

# Requests RepoMind cannot carry out: it reads and analyses code but never changes, runs or publishes it.
UNSUPPORTED = re.compile(
    r"\b(commit|push|merge|deploy|publish|release)\b(?! (history|log|message)s?\b)"
    r"|\b(open|create|make|submit)\s+(a\s+|an\s+)?(pr|pull request|branch|issue|release)\b"
    r"|\b(run|execute|start|launch)\s+(the\s+|my\s+|this\s+)?(tests?|code|app|application|server|script|program|build)\b"
    r"|\b(write|generate|implement|create|add)\s+(me\s+)?(a\s+|an\s+|the\s+|some\s+)?(new\s+)?"
    r"(function|class|feature|script|program|unit tests?|tests?|endpoint|module|code)\b"
    r"|\b(apply|make)\s+(the\s+)?(fix|change|patch)(es)?\b|\b(edit|modify|rewrite)\s+(the\s+|this\s+|my\s+)?(code|file)\b",
    re.I)

RULES: dict[str, list[str]] = {
    "security": [r"\bsecurity\b", r"\bvulnerab\w*", r"\bsecrets?\b", r"\binjection\b", r"\bxss\b", r"\bcsrf\b",
                 r"\bunsafe\b", r"\bcredentials?\b", r"\bexploit\w*", r"\bowasp\b", r"\binsecure\b",
                 r"\b(hard-?coded|leaked|exposed) (api )?(keys?|passwords?|tokens?)\b"],
    "impact": [r"\bimpact\w*", r"\baffect\w*", r"\bif i (change|modify|edit|remove|rename|refactor|delete)\b",
               r"\bwhat (depends|relies) on\b", r"\b(who|what|which files?) (uses|imports|calls|depends on)\b",
               r"\bdependents?\b", r"\bbreak\w*\b.*\bif\b", r"\bblast radius\b", r"\bripple\b"],
    "documentation": [r"\bdocument\w*", r"\bdocs\b", r"\breadme\b", r"\bguide\b", r"\bonboard\w*",
                      r"\bhow (do i|to|can i) (install|set ?up|run|build|start|deploy)\b", r"\binstall(ation)?\b",
                      r"\bgetting started\b", r"\bcontribut\w*", r"\b(write|generate|create) (an? |the )?overview\b"],
    "code_intelligence": [r"\bexplain\w*", r"\bwalk me through\b"],
    "debug": [r"\bbugs?\b", r"\berrors?\b(?! handling)", r"\bexceptions?\b(?! handling)", r"\bcrash\w*", r"\bfail(s|ed|ing|ure)?\b",
              r"\bbroken\b", r"\bdebug\w*", r"\btraceback\b", r"\bstack ?trace\b", r"\bnot working\b",
              r"\bdoes(n'?t| not) work\b", r"\bwrong (result|output|value|answer)s?\b", r"\bhangs?\b", r"\bfix\b"],
    "repository": [r"\bhow (many|big|large)\b", r"\b(which|what) (programming )?languages?\b", r"\bstars?\b",
                   r"\bforks?\b", r"\b(repo|repository) (stats|statistics|metadata|info|size|details)\b",
                   r"\bembeddings? (status|progress)\b", r"\bindex(ed|ing)? (status|state)\b", r"\bwhen was\b.*\bindexed\b",
                   r"\bdefault branch\b", r"\bprimary language\b", r"\bnumber of (files|chunks)\b"],
}
# "What does X do?" is code intelligence only when X is a file or symbol of this repository; otherwise it is a
# general question for the research agent ("what does this project do?").
TARGETED = [re.compile(p, re.I) for p in (r"\bwhat does\b.+\bdo\b", r"\bhow does\b.+\bwork\b", r"\bpurpose of\b",
                                          r"\bwhat is\b.+\b(function|method|class|file)\b", r"\bhow is\b.+\bused\b")]
COMPILED = {agent: [re.compile(p, re.I) for p in patterns] for agent, patterns in RULES.items()}
# Tie-break when several specialists match equally: the more specific intent wins.
PRIORITY = ["impact", "security", "debug", "documentation", "code_intelligence", "repository"]

DOC_SECTIONS = [
    ("setup", r"\b(install\w*|set ?up|setup|run|build|start|getting started|requirements?)\b"),
    ("api", r"\b(api|endpoints?|routes?|interface)\b"),
    ("architecture", r"\b(architecture|design|components?)\b"),
    ("structure", r"\b(structure|folders?|director(y|ies)|layout)\b"),
    ("developer_guide", r"\b(contribut\w*|developer|testing|tests|ci|workflow)\b"),
    ("modules", r"\b(modules?|important files)\b"),
]
SYMBOL_PATTERNS = [
    re.compile(r"`([A-Za-z_$][\w$]{1,80})(?:\(\))?`"),
    re.compile(r"\b([A-Za-z_$][\w$]{1,80})\(\)"),
    re.compile(r"\b(?:function|method|class|func|def)\s+`?([A-Za-z_$][\w$]{1,80})", re.I),
    re.compile(r"\b([A-Za-z_$][\w$]{1,80})`?\s+(?:function|method|class)\b", re.I),
    re.compile(r"\b([a-z][a-z0-9]*_[\w$]+|[a-z][a-z0-9]*[A-Z][\w$]*)\b"),  # snake_case / camelCase identifiers
]
NOT_SYMBOLS = {"the", "this", "that", "a", "an", "it", "does", "do", "is", "in", "of", "and", "or", "to", "for", "with",
               "main", "each", "every", "which", "what", "helper", "utility", "new", "my", "your", "its"}


def catalog() -> list[dict]:
    return [{"name": a.name, "title": a.title, "description": a.description, "uses_llm": a.uses_llm}
            for a in AGENTS.values()]


def route(query: str, files: list[str]) -> dict:
    """Deterministic routing decision with the reasons behind it."""
    file_path, path_kind = _mentioned_path(query, files)
    symbol = _mentioned_symbol(query, file_path)
    if UNSUPPORTED.search(query):
        return {"agent": None, "reason": "RepoMind analyses code; it does not change, run, generate or publish code.",
                "matched": [UNSUPPORTED.search(query).group(0)], "alternatives": [], "ambiguous": False,
                "fallback": False, "file_path": file_path, "symbol": symbol, "section": None}
    scores: dict[str, list[str]] = {}
    for agent, patterns in COMPILED.items():
        hits = [m.group(0) for p in patterns if (m := p.search(query))]
        if hits:
            scores[agent] = hits
    if (file_path and path_kind == "file") or symbol:
        hits = [m.group(0) for p in TARGETED if (m := p.search(query))]
        if hits:
            scores.setdefault("code_intelligence", []).extend(hits)
    if path_kind == "directory" and "security" not in scores:
        file_path = None  # a folder is only a useful scope for the security scan
    ranked = sorted(scores, key=lambda a: (-len(scores[a]), PRIORITY.index(a)))
    decision = {"matched": [], "alternatives": ranked[1:], "ambiguous": False, "fallback": False,
                "file_path": file_path, "symbol": symbol, "section": None}
    if not ranked:
        if file_path or symbol:
            return {**decision, "agent": "code_intelligence",
                    "reason": "No specific intent, but the request names code in this repository: explaining it."}
        return {**decision, "agent": "research", "fallback": True,
                "reason": "No specialised intent detected: answering with semantic search and retrieved evidence."}
    agent = ranked[0]
    decision["ambiguous"] = len(ranked) > 1 and len(scores[ranked[1]]) == len(scores[agent])
    reason = f"Matched {agent.replace('_', ' ')} rules: " + ", ".join(f'"{h}"' for h in scores[agent])
    if decision["ambiguous"]:
        reason += f" (also matched {', '.join(a.replace('_', ' ') for a in ranked[1:])}; chose the more specific intent)"
    if agent == "documentation":
        decision["section"] = next((s for s, p in DOC_SECTIONS if re.search(p, query, re.I)), "overview")
    return {**decision, "agent": agent, "matched": scores[agent], "reason": reason}


def _mentioned_path(query: str, files: list[str]) -> tuple[str | None, str | None]:
    """A file or folder of this repository named in the query (exact path or unique-ish file name)."""
    tokens = {t.strip(".,:;!?()'\"`") for t in re.findall(r"[\w./@-]+", query)}
    tokens = {t for t in tokens if "." in t or "/" in t}
    for token in sorted(tokens, key=len, reverse=True):
        clean = token.lstrip("./")
        if clean in files:
            return clean, "file"
        matches = [f for f in files if f.endswith("/" + clean) or (("/" not in clean) and posixpath.basename(f) == clean)]
        if matches:
            matches.sort(key=lambda f: (F.non_production(f), f.count("/"), f))
            return matches[0], "file"
        folder = clean.rstrip("/") + "/"
        if "/" in token and any(f.startswith(folder) for f in files):
            return folder, "directory"
    return None, None


def _mentioned_symbol(query: str, file_path: str | None) -> str | None:
    for pattern in SYMBOL_PATTERNS:
        for m in pattern.finditer(query):
            name = m.group(1)
            if name.lower() not in NOT_SYMBOLS and (not file_path or name not in posixpath.basename(file_path)):
                return name
    return None


def _indexed_files(repository_id: int) -> list[str]:
    with database.session_scope() as session:
        repo_code.require_repository(session, repository_id)  # 404 for unknown repositories
        return list(store.file_languages(session, repository_id))


async def run(repository_id: int, query: str) -> dict:
    started = time.monotonic()
    query = " ".join(query.split())[:MAX_QUERY]
    activity = Activity()
    files = await asyncio.to_thread(_indexed_files, repository_id)
    async with activity.step("orchestrator", "Route request") as step:
        decision = route(query, files)
        step["result"] = (f"→ {decision['agent']}" if decision["agent"] else "→ unsupported") + f" ({decision['reason']})"

    out, error = None, None
    if decision["agent"] is None:
        out = result("orchestrator", "unsupported",
                     "This request is outside what RepoMind does. RepoMind reads and analyses the indexed code: it can "
                     "answer questions, explain files or functions, investigate bugs, run a security scan, analyse the "
                     "impact of a change and write documentation, but it never edits, runs, commits or deploys code.")
    else:
        ctx = {"repository_id": repository_id, "query": query, "file_path": decision["file_path"],
               "symbol": decision["symbol"], "section": decision["section"]}
        out, error = await _run_agent(decision["agent"], ctx, activity)
        if out is None and error is None:  # the specialist could not find what to work on
            decision.update(fallback=True, reason=decision["reason"] + " — could not locate the code; fell back to research.")
            out, error = await _run_agent("research", ctx, activity)

    agent = AGENTS.get(out["agent"])
    return {**out, "agent_title": agent.title if agent else "Orchestrator", "query": query,
            "repository_id": repository_id, "routing": decision, "activity": activity.steps, "error": error,
            "took_ms": round((time.monotonic() - started) * 1000)}


async def _run_agent(name: str, ctx: dict, activity: Activity) -> tuple[dict | None, dict | None]:
    try:
        return await AGENTS[name].run(ctx, activity), None
    except SERVICE_ERRORS as exc:  # expected service errors: missing embeddings, model not configured, rate limit...
        info = error_info(exc)
        return result(name, "failed", f"The {AGENTS[name].title} could not complete: {info['message']}"), info
    except Exception as exc:  # a bug: report it as a failed agent instead of a broken request
        log.exception("agent %s failed", name)
        info = {"code": "agent_error", "message": f"Unexpected error in the {AGENTS[name].title}.", "status_code": 500,
                "retry_after_seconds": None}
        if activity.steps and activity.steps[-1]["status"] == "running":
            activity.steps[-1]["status"] = "failed"
        return result(name, "failed", info["message"]), info
