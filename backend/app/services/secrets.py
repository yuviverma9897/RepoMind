"""Detect and mask secrets (API keys, tokens, private keys, passwords) in code and text.

mask_secrets() is applied to everything sent to the LLM (llm.generate), to LLM output, to sources
returned with AI answers and to security/debug findings. Masking keeps the original length (and
newlines), so line and column numbers stay valid.

Detection is pattern-based: it can miss secrets in unusual formats and can flag test values.
"""

import math
import re
from dataclasses import dataclass

MASK_CHAR = "*"


@dataclass(frozen=True)
class Detector:
    kind: str
    pattern: re.Pattern
    group: int | str = 0  # the part of the match that is secret
    check_entropy: bool = False
    hints: tuple = ()  # lowercase substrings, one of which must occur (fast pre-filter)


DETECTORS = [
    Detector("private_key", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----"
        r"(?P<body>[\s\S]+?)(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|\Z)"), "body", hints=("private key",)),
    Detector("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), hints=("akia", "asia")),
    Detector("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"), hints=("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")),
    Detector("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), hints=("aiza",)),
    Detector("slack_token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}\b"), hints=("xox",)),
    Detector("stripe_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b"), hints=("_live_", "_test_")),
    Detector("openai_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}\b"), hints=("sk-",)),
    Detector("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), hints=("eyj",)),
    Detector("url_password", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@'\"]+:(?P<pw>[^\s@/'\"]{3,})@"), "pw", hints=("://",)),
    Detector("assigned_secret", re.compile(
        r"(?i)(?:password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|"
        r"private[_-]?key|client[_-]?secret|credentials?)[\w.-]*['\"]?\s*(?::=|=>|===?|!==?|[:=])\s*(?P<q>['\"])(?P<value>[^'\"\s]{8,200})(?P=q)"),
        "value", check_entropy=True, hints=("pass", "pwd", "secret", "key", "token", "credential")),
]

_PLACEHOLDER = re.compile(
    r"(?i)^(?:x+|\*+|\.+|-+|<[^>]*>|\{\{.*\}\}|\$\{.*\}|%\(.*\)s|changeme|change[_-]?me|your[_-].*|.*example.*|"
    r".*placeholder.*|dummy.*|fake.*|test.*|sample.*|redacted|none|null|password|secret|todo|xxx.*|dev|development)$")


def shannon_entropy(value: str) -> float:
    counts = {c: value.count(c) for c in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


_WORDS_ONLY = re.compile(r"[A-Za-z]+(?:[_. -][A-Za-z]+)*")  # e.g. 'credentialless', 'my-secret-password'


def is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER.match(value) or _WORDS_ONLY.fullmatch(value)) or len(set(value)) <= 3 or shannon_entropy(value) < 3.0


def find_secrets(text: str) -> list[dict]:
    """[{kind, start, end}] character spans of secret values (overlaps removed)."""
    spans = []
    lowered = text.lower()
    for detector in DETECTORS:
        if detector.hints and not any(h in lowered for h in detector.hints):
            continue
        for match in detector.pattern.finditer(text):
            start, end = match.span(detector.group)
            value = text[start:end]
            if detector.check_entropy and is_placeholder(value):
                continue
            if any(s <= start < e or start <= s < end for s, e, _ in spans):
                continue
            spans.append((start, end, detector.kind))
    return [{"kind": kind, "start": s, "end": e} for s, e, kind in sorted(spans)]


def _mask_value(value: str) -> str:
    if "\n" in value:  # private key bodies: keep the line structure
        return "\n".join(MASK_CHAR * len(line) for line in value.split("\n"))
    keep = min(4, len(value) // 4)
    return value[:keep] + MASK_CHAR * (len(value) - keep)


def mask_secrets(text: str) -> tuple[str, list[dict]]:
    """Return (masked text, secrets found). Lengths and newlines are preserved."""
    if not text:
        return text, []
    found = find_secrets(text)
    if not found:
        return text, []
    parts, last = [], 0
    for item in found:
        parts.append(text[last:item["start"]])
        parts.append(_mask_value(text[item["start"]:item["end"]]))
        last = item["end"]
    parts.append(text[last:])
    return "".join(parts), found


def mask(text: str) -> str:
    return mask_secrets(text)[0]
