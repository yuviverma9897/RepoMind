"""Specialist agents. Each one is a thin wrapper that calls existing services and turns their output into the
common agent result; the analysis itself lives in app/services (no duplicated logic, no extra LLM clients)."""

import asyncio
import re

from app.agents.base import Activity, result
from app.db import database, store
from app.services import (architecture, code_intelligence, debug, dependencies, documentation, embedding_service,
                          findings as F, llm, rag, repo_code, search, security)
from app.services.llm import LLMError


def llm_configured() -> bool:
    try:
        llm.get_llm()
        return True
    except LLMError:
        return False


def _short(text: str | None, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _grounded_meta(out: dict) -> dict:
    return {"model": out.get("model"), "usage": out.get("usage"), "citations": out.get("citations"),
            "evidence": out.get("evidence"), "truncated": out.get("truncated", False)}


class Agent:
    name = ""
    title = ""
    description = ""
    uses_llm = "never"  # never | optional | always

    async def run(self, ctx: dict, activity: Activity) -> dict:
        raise NotImplementedError


class RepositoryAgent(Agent):
    name, title, uses_llm = "repository", "Repository Agent", "never"
    description = "Answers questions about the repository itself (metadata, languages, size, index and embedding state) from stored data."

    async def run(self, ctx, activity):
        async with activity.step(self.name, "Read repository metadata and index statistics") as step:
            facts = await asyncio.to_thread(_repository_facts, ctx["repository_id"])
            step["result"] = f"{facts['files']} files, {facts['chunks']} chunks, {len(facts['languages'])} languages"
        langs = ", ".join(f"{k} ({v})" for k, v in facts["languages"].items()) or "none detected"
        emb = facts["embeddings"]
        lines = [
            f"**{facts['full_name']}**" + (f" — {facts['description']}" if facts["description"] else ""),
            "",
            f"- Primary language (GitHub): {facts['language'] or 'not reported'}",
            f"- Stars / forks (at import): {facts['stars']} / {facts['forks']}",
            f"- Indexed files: {facts['files']} · stored chunks: {facts['chunks']}",
            f"- Languages by indexed files: {langs}",
            f"- Default branch: {facts['branch']} · indexed commit: {facts['commit'] or 'unknown'} · indexed at: {facts['indexed_at']}",
            f"- Embeddings: {emb['embedded_chunks']}/{emb['total_chunks']} chunks ({emb['progress_percent']}%), "
            f"model {emb['model']}" + ("" if emb["provider_configured"] else " — provider not configured"),
        ]
        return result(self.name, "completed", "\n".join(lines), facts=facts)


def _repository_facts(repository_id: int) -> dict:
    with database.session_scope() as session:
        repo = repo_code.require_repository(session, repository_id)
        languages = store.file_languages(session, repository_id)
        counts: dict[str, int] = {}
        for lang in languages.values():
            counts[lang] = counts.get(lang, 0) + 1
        facts = {"full_name": repo.full_name, "description": repo.description, "language": repo.language,
                 "stars": repo.stars, "forks": repo.forks, "branch": repo.indexed_branch or repo.default_branch,
                 "commit": (repo.indexed_commit_sha or "")[:12] or None,
                 "indexed_at": repo.indexed_at.isoformat() if repo.indexed_at else None,
                 "files": len(languages), "languages": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}
    emb = embedding_service.embedding_status(repository_id)
    facts["chunks"] = emb["total_chunks"]
    facts["embeddings"] = {k: emb[k] for k in ("total_chunks", "embedded_chunks", "progress_percent", "model",
                                                "provider_configured")}
    return facts


class ResearchAgent(Agent):
    name, title, uses_llm = "research", "Research Agent (RAG)", "always"
    description = "Answers general questions with semantic search plus a grounded, cited answer (the Copilot pipeline)."

    async def run(self, ctx, activity):
        async with activity.step(self.name, "Semantic search and grounded answer") as step:
            out = await rag.ask(ctx["repository_id"], ctx["query"], 8)
            step["result"] = f"{len(out['sources'])} sources retrieved, {len(out['citations']['cited'])} cited"
        status = "partial" if out.get("partial") else "completed"
        return result(self.name, status, out["answer"], out["sources"], message=out.get("message"),
                      retrieval_ms=out.get("retrieval_ms"), **_grounded_meta(out))


class CodeIntelligenceAgent(Agent):
    name, title, uses_llm = "code_intelligence", "Code Intelligence Agent", "always"
    description = "Explains a specific file, function or class: locates it (path, symbol name or semantic search), then explains it."

    async def run(self, ctx, activity):
        rid, file_path, symbol = ctx["repository_id"], ctx.get("file_path"), ctx.get("symbol")
        if not file_path:
            async with activity.step(self.name, "Locate the code to explain") as step:
                file_path, symbol, how = await _locate(rid, ctx["query"], symbol)
                step["result"] = f"{how}: {file_path}" + (f" · {symbol}" if symbol else "") if file_path else how
            if not file_path:
                return None  # orchestrator falls back to the research agent
        elif symbol:
            known = await asyncio.to_thread(code_intelligence.list_symbols, rid, file_path)
            if not any(s["name"] == symbol for s in known["symbols"]):
                symbol = None  # the name was not defined in that file: explain the file instead
        target = f"{file_path}" + (f" · {symbol}" if symbol else "")
        async with activity.step(self.name, f"Explain {target}") as step:
            out = await code_intelligence.explain(rid, file_path, symbol=symbol)
            step["result"] = f"explained {out['target']['kind']} lines {out['target']['start_line']}-{out['target']['end_line']}"
        return result(self.name, "completed", out["answer"], out["sources"], target=out["target"],
                      structure=out.get("structure"), **_grounded_meta(out))


async def _locate(repository_id: int, query: str, symbol: str | None) -> tuple[str | None, str | None, str]:
    if symbol:
        found = await asyncio.to_thread(_find_symbol, repository_id, symbol)
        if found:
            return found, symbol, "symbol definition"
        # A named symbol that does not exist: explaining whatever search returns would be misleading.
        return None, None, f"not located (no definition of '{symbol}' in the indexed files)"
    try:
        hits = (await search.search_repository(repository_id, query, 3))["results"]
    except search.SearchError as error:  # embeddings not ready
        return None, None, f"not located (no symbol match; semantic search unavailable: {error.message})"
    if not hits:
        return None, None, "not located"
    best = hits[0]
    return best["file_path"], best.get("symbol_name") if not best.get("parent_symbol") else None, "semantic search"


def _find_symbol(repository_id: int, name: str) -> str | None:
    """File defining `name` (production code preferred): chunk symbol names, then definitions inside chunks."""
    with database.session_scope() as session:
        rows = store.symbol_chunks(session, repository_id, [name], 20)
        if not rows:
            pattern = re.compile(rf"\b(def|func|function|class|fn)\s+(\([^)]*\)\s*)?{re.escape(name)}\b")
            rows = [r for r in store.iter_chunks(session, repository_id) if name in r.content and pattern.search(r.content)]
    if not rows:
        return None
    rows.sort(key=lambda r: (F.non_production(r.file_path), r.file_path.count("/"), r.file_path))
    return rows[0].file_path


class DebugAgent(Agent):
    name, title, uses_llm = "debug", "Debug Agent", "optional"
    description = "Investigates a described problem: retrieved evidence, rule hints and AI-proposed potential causes. Never edits code."

    async def run(self, ctx, activity):
        use_ai = llm_configured()
        if not use_ai:
            activity.skipped(self.name, "AI root-cause analysis", "No language model configured; rule hints only.")
        async with activity.step(self.name, "Debug investigation" + (" with AI analysis" if use_ai else " (rule hints)")) as step:
            out = await debug.investigate(ctx["repository_id"], ctx["query"], file_path=ctx.get("file_path"),
                                          symbol=ctx.get("symbol"), use_ai=use_ai)
            step["result"] = f"{len(out['findings'])} potential findings from {len(out['sources'])} sources"
        answer = out.get("summary") or _findings_text(out["findings"], "No rule hints matched the retrieved code.")
        status = "partial" if out.get("partial") or not use_ai else "completed"
        return result(self.name, status, answer, out["sources"], findings=out["findings"][:10],
                      total_findings=len(out["findings"]), message=out.get("message"), limitations=out["limitations"],
                      **_grounded_meta(out))


def _findings_text(items: list[dict], empty: str) -> str:
    if not items:
        return empty
    lines = [f"{len(items)} potential findings (need human review):", ""]
    for f in items[:8]:
        lines.append(f"- **{f['severity']}** {f['title']} — `{f['file_path']}` lines {f['start_line']}-{f['end_line']}")
    if len(items) > 8:
        lines.append(f"- … and {len(items) - 8} more")
    return "\n".join(lines)


class SecurityAgent(Agent):
    name, title, uses_llm = "security", "Security Agent", "optional"
    description = "Runs the pattern-based security scan (secrets, injection, unsafe input...) and, if a model is configured, an AI review."

    async def run(self, ctx, activity):
        use_ai = llm_configured()
        if not use_ai:
            activity.skipped(self.name, "AI review of findings", "No language model configured; pattern checks only.")
        prefix = ctx.get("file_path")
        async with activity.step(self.name, "Security scan" + (f" of {prefix}" if prefix else "") +
                                 (" with AI review" if use_ai else "")) as step:
            out = await security.analyze(ctx["repository_id"], path_prefix=prefix, use_ai=use_ai)
            step["result"] = f"{out['total_findings']} potential findings in {out['files_scanned']} files"
        sev = ", ".join(f"{k} {v}" for k, v in out["counts"]["by_severity"].items()) or "none"
        answer = (_findings_text(out["findings"], f"No potential findings in {out['files_scanned']} scanned files.")
                  + f"\n\nBy severity: {sev}.\n\n_These are potential findings from pattern checks"
                  + (" and an AI review" if use_ai else "") + " — not proof of a vulnerability and not a complete audit._")
        return result(self.name, "completed" if use_ai else "partial", answer, out["ai"]["sources"],
                      findings=out["findings"][:10], total_findings=out["total_findings"], counts=out["counts"],
                      ai=out["ai"], limitations=out["limitations"])


class DocumentationAgent(Agent):
    name, title, uses_llm = "documentation", "Documentation Agent", "always"
    description = "Writes one documentation section (overview, setup, API, architecture...) from retrieved repository evidence."

    async def run(self, ctx, activity):
        section = ctx.get("section") or "overview"
        title = documentation.SECTIONS[section]["title"]
        async with activity.step(self.name, f"Generate documentation section: {title}") as step:
            out = await documentation.generate_section(ctx["repository_id"], section)
            step["result"] = (f"{out['status'].replace('_', ' ')}, {len(out['sources'])} sources"
                              + (" (cached)" if out["cached"] else ""))
        status = "completed" if out["status"] == "generated" else "partial"
        return result(self.name, status, out["content"], out["sources"], section=section, title=title,
                      notes=out["notes"], facts=out["facts"], cached=out["cached"], model=out["model"],
                      usage=out["usage"], citations=out["citations"], evidence=out["evidence"])


class ImpactAgent(Agent):
    name, title, uses_llm = "impact", "Impact Agent", "optional"
    description = "AI-assisted impact analysis of changing a file or symbol: import-graph dependents, references, optional explanation."

    async def run(self, ctx, activity):
        rid, file_path, symbol = ctx["repository_id"], ctx.get("file_path"), ctx.get("symbol")
        if not file_path:
            return result(self.name, "needs_input", "Impact analysis needs a file. Name the file you plan to change, "
                          "e.g. \"what breaks if I change src/app.py?\"")
        if llm_configured():
            async with activity.step(self.name, f"Dependency analysis and AI explanation for {file_path}") as step:
                out = await architecture.explain_impact(rid, file_path, symbol)
                impact = out["impact"]
                step["result"] = f"{len(impact['direct_dependents'])} direct, {len(impact['transitive_dependents'])} transitive dependents"
            return result(self.name, "completed", out["answer"], out["sources"], impact=impact, **_grounded_meta(out))
        activity.skipped(self.name, "AI explanation", "No language model configured; dependency analysis only.")
        async with activity.step(self.name, f"Dependency analysis for {file_path}") as step:
            impact = await asyncio.to_thread(dependencies.impact, rid, file_path, symbol)
            step["result"] = f"{len(impact['direct_dependents'])} direct, {len(impact['transitive_dependents'])} transitive dependents"
        lines = [f"Static import-graph analysis of `{file_path}`" + (f" (symbol `{symbol}`)" if symbol else "") + ":", "",
                 f"- Direct dependents: {len(impact['direct_dependents'])}",
                 *[f"  - `{d['file_path']}`" for d in impact["direct_dependents"][:10]],
                 f"- Transitive dependents (depth {impact['depth']}): {len(impact['transitive_dependents'])}",
                 f"- References to the symbol: {len(impact['references'])}" if symbol else "",
                 "", *[f"_{x}_" for x in impact["limitations"][:2]]]
        return result(self.name, "partial", "\n".join(l for l in lines if l is not None), impact=impact)


AGENTS: dict[str, Agent] = {a.name: a for a in (RepositoryAgent(), ResearchAgent(), CodeIntelligenceAgent(),
                                                  DebugAgent(), SecurityAgent(), DocumentationAgent(), ImpactAgent())}
