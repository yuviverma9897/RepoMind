"""Shared structure and validation for debug/security findings (rule-based and AI-proposed).

AI findings are only accepted when they point at a source the model was actually given and at
lines inside that source; everything else is dropped and counted. All findings require review.
"""

import json
import re

from app.services import secrets

SEVERITIES = ["critical", "high", "medium", "low", "info"]
CONFIDENCES = ["high", "medium", "low"]
# Tests, examples, docs and build/CI tooling: findings there are shown with lower severity.
_NON_PRODUCTION = re.compile(
    r"(^|/)(tests?|__tests__|__mocks__|spec|specs|fixtures?|examples?|samples?|docs?|testdata|mocks?|benchmarks?|"
    r"scripts?|tools?|ci|\.github|\.circleci)(/|$)|"
    r"(_test|\.test|\.spec|_spec|test_)[^/]*$|(^|/)tests?\.[cm]?[jt]sx?$|(^|/)(conftest\.py|[^/]*\.config\.[cm]?[jt]s|gulpfile\.[jt]s|"
    r"gruntfile\.[jt]s|(build|deploy|release|publish)\.[cm]?[jt]s|setup\.py|noxfile\.py)$", re.I)


def non_production(path: str) -> bool:
    return bool(_NON_PRODUCTION.search(path))


def downgrade(severity: str) -> str:
    return SEVERITIES[min(SEVERITIES.index(severity) + 1, len(SEVERITIES) - 1)]


def finding(*, title: str, category: str, severity: str, file_path: str, start_line: int, end_line: int,
            explanation: str, recommendation: str, confidence: str, origin: str, rule_id: str | None = None,
            snippet: str | None = None, reasoning: str | None = None, source_id: str | None = None) -> dict:
    test = non_production(file_path)
    return {
        "id": None,  # numbered (F1, F2, ...) after sorting
        "rule_id": rule_id,
        "title": title,
        "category": category,
        "severity": downgrade(severity) if test and origin == "rule" else severity,
        "confidence": confidence,
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "snippet": secrets.mask(snippet)[:300] if snippet else None,
        "explanation": secrets.mask(explanation),
        "reasoning": secrets.mask(reasoning) if reasoning else None,
        "recommendation": secrets.mask(recommendation),
        "requires_review": True,  # potential finding, never a confirmed bug/vulnerability
        "origin": origin,  # rule | ai
        "source_id": source_id,
        "non_production": test,  # test / example / docs / tooling code
        "ai_review": None,
    }


def sort_and_number(items: list[dict]) -> list[dict]:
    items.sort(key=lambda f: (SEVERITIES.index(f["severity"]), CONFIDENCES.index(f["confidence"]), f["non_production"],
                              f["file_path"], f["start_line"]))
    for i, f in enumerate(items, start=1):
        f["id"] = f"F{i}"
    return items


def parse_json_object(text: str) -> dict | None:
    """Parse the model's JSON answer, tolerating ```json fences or text around the object."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        value = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def validate_ai_findings(items, sources: list[dict], categories: set[str], *, extra_text_fields=()) -> tuple[list[dict], int]:
    """Turn AI-proposed findings into findings; returns (accepted, rejected_count)."""
    by_id = {s["source_id"]: s for s in sources}
    accepted, rejected = [], 0
    for item in items if isinstance(items, list) else []:
        try:
            source = by_id.get(str(item.get("source", "")).strip("[] ").upper())
            start, end = int(item.get("start_line")), int(item.get("end_line", item.get("start_line")))
            category = str(item.get("category", "")).lower().replace(" ", "_")
            severity = str(item.get("severity", "")).lower()
            confidence = str(item.get("confidence", "low")).lower()
            title = str(item.get("title", "")).strip()
        except (TypeError, ValueError, AttributeError):
            rejected += 1
            continue
        if (not source or not title or category not in categories or severity not in SEVERITIES
                or not source["start_line"] <= start <= end <= source["end_line"]):
            rejected += 1  # unknown source, lines outside the evidence, or invalid labels
            continue
        lines = source["content"].split("\n")
        snippet = "\n".join(lines[start - source["start_line"]:end - source["start_line"] + 1])
        accepted.append(finding(
            title=title[:200], category=category, severity=severity, file_path=source["file_path"],
            start_line=start, end_line=end, explanation=str(item.get("explanation", ""))[:2000],
            recommendation=str(item.get("recommendation", item.get("suggestion", "")))[:2000],
            confidence=confidence if confidence in CONFIDENCES else "low", origin="ai", snippet=snippet,
            reasoning=str(item.get("reasoning", ""))[:2000] or None, source_id=source["source_id"]))
    return accepted, rejected
