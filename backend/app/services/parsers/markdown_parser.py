"""Markdown structure: one section per heading (levels 1-3), ignoring code fences."""

import re

from app.services.parsers.base import CodeUnit

HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def parse_markdown(text: str) -> list[CodeUnit]:
    lines = text.split("\n")
    sections: list[CodeUnit] = []
    in_fence = False

    for number, line in enumerate(lines, start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        match = None if in_fence else HEADING.match(line)
        if match:
            if sections:
                sections[-1].end_line = number - 1
            sections.append(CodeUnit("section", match.group(2), number, number))

    if sections:
        sections[-1].end_line = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
    for section in sections:  # trailing blank lines belong to no section
        while section.end_line > section.start_line and not lines[section.end_line - 1].strip():
            section.end_line -= 1
    return sections
