from dataclasses import dataclass, field


@dataclass
class CodeUnit:
    """A structural element found by a parser (function, class, section, ...).

    Lines are 1-based and inclusive. `children` holds members of containers
    (e.g. methods of a class) and is used when a container is too large to
    keep in one chunk.
    """

    kind: str
    name: str | None
    start_line: int
    end_line: int
    parent: str | None = None
    children: list["CodeUnit"] = field(default_factory=list)


def sanitize_units(units: list[CodeUnit], first: int, last: int) -> list[CodeUnit]:
    """Keep only well-formed, in-range, non-overlapping units (sorted).

    Parsers are allowed to be imperfect; this guarantees the chunker always
    receives consistent input.
    """
    clean: list[CodeUnit] = []
    for unit in sorted(units, key=lambda u: (u.start_line, -u.end_line)):
        if not (first <= unit.start_line <= unit.end_line <= last):
            continue
        if clean and unit.start_line <= clean[-1].end_line:
            continue  # overlaps the previous unit
        unit.children = sanitize_units(unit.children, unit.start_line, unit.end_line)
        clean.append(unit)
    return clean


def split_lines(text: str) -> list[str]:
    """Split into lines the same way editors number them (a final newline adds no line)."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def count_lines(text: str) -> int:
    return len(split_lines(text))
