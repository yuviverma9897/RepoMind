"""Validate [S#] citations in generated text against the sources the model was actually given.

Only ids that exist in `sources` survive. Invalid ids (e.g. [S99]) are removed from the text and
reported, so the UI never links to a source that does not exist.
"""

import re

from app import config

# [S1]   [S1, S3]   [S1,S2]   (also tolerates "[s2]")
CITATION = re.compile(r"\[\s*([Ss]\d+(?:\s*[,;]\s*[Ss]\d+)*)\s*\]")
_REMOVED = "\x00"


def process_citations(text: str, sources: list[dict]) -> dict:
    """Return {"text", "cited", "invalid"}; `text` has normalized citations like [S1][S3]."""
    known = {s["source_id"] for s in sources}
    cited: list[str] = []
    invalid: list[str] = []

    def replace(match: re.Match) -> str:
        ids = [part.strip().upper() for part in re.split(r"[,;]", match.group(1))]
        good = []
        for source_id in ids:
            bucket = cited if source_id in known else invalid
            if source_id not in bucket:
                bucket.append(source_id)
            if source_id in known and source_id not in good:
                good.append(source_id)
        return "".join(f"[{i}]" for i in good) if good else _REMOVED

    cleaned = CITATION.sub(replace, text)
    cleaned = re.sub(rf"[ \t]*{_REMOVED}", "", cleaned)  # drop the gap a removed citation leaves
    return {"text": cleaned, "cited": cited, "invalid": invalid}


def assess_evidence(sources: list[dict], cited: list[str], threshold: float | None = None) -> dict:
    """How well the retrieved sources support an answer.

    supported: relevant sources exist and the answer cites them
    weak:      even the best source has low similarity (retrieval probably missed)
    uncited:   the answer cites none of the sources
    none:      nothing was retrieved
    """
    threshold = config.RAG_WEAK_SIMILARITY if threshold is None else threshold
    if not sources:
        return {"level": "none", "best_similarity": None, "threshold": threshold,
                "message": "No code was retrieved for this request."}
    similarities = [s["similarity"] for s in sources if s.get("similarity") is not None]
    best = max(similarities) if similarities else None
    pinned = len(similarities) < len(sources)  # code chosen by the user/target, not by similarity
    if best is not None and best < threshold and not pinned:
        return {"level": "weak", "best_similarity": best, "threshold": threshold,
                "message": f"The closest code match is weak (similarity {best:.2f} < {threshold:.2f}). "
                           "The repository may not contain code about this; treat the answer with caution."}
    if not cited:
        return {"level": "uncited", "best_similarity": best, "threshold": threshold,
                "message": "The answer does not cite any source, so it cannot be checked against the code."}
    return {"level": "supported", "best_similarity": best, "threshold": threshold, "message": None}


def mark_cited(sources: list[dict], cited: list[str]) -> list[dict]:
    used = set(cited)
    return [{**s, "cited": s["source_id"] in used} for s in sources]
