"""Parser registry: picks a parsing strategy per language, with a safe fallback.

To add a language: write a function `parse_x(text) -> list[CodeUnit]` and
register it in PARSERS. Languages without a parser are chunked by lines.
"""

import logging
from dataclasses import dataclass, field
from functools import partial

from app.services.parsers.base import CodeUnit, count_lines, sanitize_units
from app.services.parsers.brace_parser import SUPPORTED as BRACE_LANGUAGES
from app.services.parsers.brace_parser import parse_brace
from app.services.parsers.markdown_parser import parse_markdown
from app.services.parsers.python_parser import parse_python

log = logging.getLogger(__name__)

# Parser names reported in the API
LINE_WINDOW = "line-window"  # language has no structural parser (JSON, HTML, CSS, ...)
FALLBACK = "fallback"  # a structural parser exists but failed on this file

PARSERS = {
    "python": ("python-ast", parse_python),
    "markdown": ("markdown-headings", parse_markdown),
    **{lang: ("brace-heuristic", partial(parse_brace, language=lang)) for lang in BRACE_LANGUAGES},
}


@dataclass
class ParseResult:
    parser: str
    units: list[CodeUnit] = field(default_factory=list)
    error: str | None = None


def parse_source(text: str, language: str) -> ParseResult:
    entry = PARSERS.get(language)
    if entry is None:
        return ParseResult(LINE_WINDOW)

    name, parse = entry
    try:
        units = parse(text)
    except Exception as exc:  # any parser failure must not break the repository analysis
        log.debug("%s parser failed: %r", name, exc)
        return ParseResult(FALLBACK, error=f"{name} failed: {type(exc).__name__}")

    return ParseResult(name, sanitize_units(units, 1, count_lines(text)))
