"""Heuristic structure parser for brace-delimited languages.

Supports JavaScript/TypeScript (incl. JSX/TSX), Java, C, C++, C#, Go and Rust.

This is NOT a full parser. It works in three steps:
  1. Mask strings, comments (and JS regex literals) with spaces, preserving
     newlines, so braces inside them cannot confuse brace matching.
  2. Scan lines at brace depth 0 for language-specific declaration patterns.
  3. Find each declaration's body by brace matching on the masked text.
Containers (classes, impls, namespaces, ...) are scanned again for members.

Anything it cannot recognise simply becomes "top-level code" for the chunker,
so an imperfect parse degrades gracefully instead of failing.
A tree-sitter based parser can replace this per language later.
"""

import bisect
import re
from dataclasses import dataclass, field

from app.services.parsers.base import CodeUnit

# ---------------------------------------------------------------- masking

_LINE_COMMENT = r"//[^\n]*"
_BLOCK_COMMENT = r"/\*[\s\S]*?(?:\*/|$)"
_DOUBLE_QUOTED = r'"(?:\\.|[^"\\\n])*"?'  # unterminated strings stop at end of line
_SINGLE_QUOTED = r"'(?:\\.|[^'\\\n])*'?"
_CHAR_LITERAL = r"'(?:\\.|[^'\\\n])'"  # exactly one char, so Rust lifetimes ('a) survive
_TEMPLATE = r"`(?:\\.|[^`\\])*`?"  # JS template literal (may span lines)
_GO_RAW = r"`[^`]*`?"
_CS_VERBATIM = r'@"(?:""|[^"])*"?'
_RUST_RAW = r'(?<![\w])r(?P<hashes>#*)"[\s\S]*?"(?P=hashes)'
# A "/" that starts a regex literal: preceded by an operator/opening char, not by a value.
_JS_REGEX = r"(?<=[(,=:\[!&|?{};])\s*/(?![/*])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])+/[a-z]*"


def _mask_pattern(*parts: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in parts))


def _blank(match: re.Match) -> str:
    s = match.group(0)
    return " " * len(s) if "\n" not in s else re.sub(r"[^\n]", " ", s)


# ---------------------------------------------------------------- rules

@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern
    kind: str | None = None  # None: derived from the "kw" group
    container: bool = False  # scan the body for members
    arrow: bool = False  # JS: body must follow "=>" (or the "function" keyword)


def R(pattern: str, kind: str | None = None, container: bool = False, arrow: bool = False) -> Rule:
    return Rule(re.compile(pattern), kind, container, arrow)


def as_method(rule: Rule) -> Rule:
    return Rule(rule.pattern, "method", rule.container, rule.arrow)


KIND_BY_KEYWORD = {
    "class": "class", "record": "class", "interface": "interface", "@interface": "interface",
    "enum": "enum", "struct": "struct", "union": "struct", "trait": "trait",
}

_NOT_CONTROL = (
    r"(?!(?:if|else|for|foreach|while|do|switch|case|catch|try|return|throw|new|delete|"
    r"sizeof|typeof|await|yield|using|lock|goto|function|match|loop)\b)"
)
# C-style "type name(" declarations: C, C++, Java, C# functions/methods/constructors.
_C_CALLABLE = (
    rf"^{_NOT_CONTROL}(?:[\w$<>\[\],.?*&:~@]+\s+)*?[*&\s]*"
    r"(?P<name>[~\w$]+(?:::[~\w$]+)*)\s*\("
)

# JavaScript / TypeScript
JS_TOP = [
    R(r"^(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?P<kw>class)\s+(?P<name>[\w$]+)", container=True),
    R(r"^(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\b\s*\*?\s*(?P<name>[\w$]+)", "function"),
    R(r"^(?:export\s+)?(?:const|let|var)\s+(?P<name>[\w$]+)\s*(?::[^=]+)?=\s*(?:async\b\s*)?"
      r"(?:function\b|\(|[\w$]+\s*=>|<)", "function", arrow=True),
    R(r"^(?:export\s+)?(?:default\s+)?(?:declare\s+)?interface\s+(?P<name>[\w$]+)", "interface"),
    R(r"^(?:export\s+)?(?:declare\s+)?type\s+(?P<name>[\w$]+)[^=]*=", "type"),
    R(r"^(?:export\s+)?(?:declare\s+)?(?:const\s+)?enum\s+(?P<name>[\w$]+)", "enum"),
    R(r"^(?:export\s+)?(?:declare\s+)?(?:namespace|module)\s+(?P<name>[\w$.]+)", "namespace", container=True),
]
JS_MEMBERS = [
    R(rf"^{_NOT_CONTROL}(?:(?:public|private|protected|static|readonly|abstract|override|async|declare|get|set)\s+)*"
      r"\*?\s*(?P<name>#?[\w$]+)\s*(?:<[^>(]*>)?\s*\(", "method"),
    R(r"^(?:(?:public|private|protected|static|readonly)\s+)*(?P<name>#?[\w$]+)\s*(?::[^=]+)?=\s*(?:async\b\s*)?"
      r"(?:\(|[\w$]+\s*=>)", "method", arrow=True),
]
JS_IMPORTS = re.compile(
    r"^(?:import\b(?!\s*\()|export\s+(?:\*|\{[^}]*\})\s+from\b|(?:const|let|var)\s+[\w${}\s,:]+=\s*require\s*\()"
)

# Java
_JAVA_MODS = r"(?:(?:public|private|protected|static|final|abstract|sealed|non-sealed|strictfp|default)\s+)*"
JAVA_TYPE = R(rf"^{_JAVA_MODS}(?P<kw>class|interface|enum|record|@interface)\s+(?P<name>\w+)", container=True)
JAVA_TOP = [JAVA_TYPE]
JAVA_MEMBERS = [JAVA_TYPE, R(_C_CALLABLE, "method")]

# C#
_CS_MODS = (
    r"(?:(?:public|private|protected|internal|static|readonly|sealed|abstract|virtual|override|"
    r"async|partial|unsafe|extern|new|file|ref)\s+)*"
)
CS_TYPE = R(rf"^{_CS_MODS}(?P<kw>class|interface|enum|record|struct)\b(?:\s+(?:class|struct))?\s+(?P<name>\w+)",
            container=True)
CS_TOP = [R(r"^namespace\s+(?P<name>[\w.]+)", "namespace", container=True), CS_TYPE, R(_C_CALLABLE, "function")]
CS_MEMBERS = [CS_TYPE, R(_C_CALLABLE, "method")]

# C and C++ (C files use the same rules; C++-only keywords simply never match)
CPP_TYPE = R(r"^(?:typedef\s+)?(?:template\s*<[^>]*>\s*)?(?P<kw>class|struct|union|enum)\b(?:\s+class)?\s+"
             r"(?:[A-Z_][A-Z0-9_]*\s+)?(?P<name>\w+)", container=True)
CPP_TOP = [
    R(_C_CALLABLE, "function"),
    CPP_TYPE,
    R(r"^(?:inline\s+)?namespace\b(?:\s+(?P<name>[\w:]+))?", "namespace", container=True),
    R(r"^extern\s*(?=\{)", "namespace", container=True),  # extern "C" { (string is masked)
]
CPP_MEMBERS = [CPP_TYPE, R(_C_CALLABLE, "method")]

# Go (methods are top-level, with a receiver)
GO_TOP = [
    R(r"^func\s*\((?P<recv>[^)]*)\)\s*(?P<name>\w+)", "method"),
    R(r"^func\s+(?P<name>\w+)", "function"),
    R(r"^type\s+(?P<name>\w+)(?:\[[^\]]*\])?\s+(?P<kw>struct|interface)\b"),
]

# Rust
_RUST_VIS = r"(?:pub(?:\s*\([^)]*\))?\s+)?"
RUST_FN = R(rf"^{_RUST_VIS}(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+)?fn\s+(?P<name>\w+)",
            "function")
RUST_TOP = [
    RUST_FN,
    R(rf"^{_RUST_VIS}(?:unsafe\s+)?(?P<kw>struct|enum|union|trait)\s+(?P<name>\w+)", container=True),
    R(r"^(?:unsafe\s+)?impl\b(?:\s*<[^{]*?>)?\s+(?P<name>[^{]+?)\s*(?:\{|$|\bwhere\b)", "impl", container=True),
    R(rf"^{_RUST_VIS}mod\s+(?P<name>\w+)", "module", container=True),
]
RUST_MEMBERS = [as_method(RUST_FN)]


@dataclass(frozen=True)
class LanguageRules:
    mask: re.Pattern
    top: list[Rule]
    members: list[Rule]
    imports: re.Pattern | None = None
    prefix: re.Pattern | None = None  # leading annotations/attributes to skip before matching
    namespace_kinds: frozenset = field(default_factory=lambda: frozenset({"namespace", "module"}))


_JS = LanguageRules(
    mask=_mask_pattern(_LINE_COMMENT, _BLOCK_COMMENT, _DOUBLE_QUOTED, _SINGLE_QUOTED, _TEMPLATE, _JS_REGEX),
    top=JS_TOP, members=JS_MEMBERS, imports=JS_IMPORTS,
    prefix=re.compile(r"^(?:@[\w.]+(?:\s*\([^()]*\))?\s+)+"),
)
_C_FAMILY_MASK = _mask_pattern(_LINE_COMMENT, _BLOCK_COMMENT, _DOUBLE_QUOTED, _CHAR_LITERAL)
_CPP = LanguageRules(
    mask=_C_FAMILY_MASK, top=CPP_TOP, members=CPP_MEMBERS,
    imports=re.compile(r"^(?:#\s*include\b|using\s+namespace\b|import\b)"),
)

LANGUAGES: dict[str, LanguageRules] = {
    "javascript": _JS, "jsx": _JS, "typescript": _JS, "tsx": _JS,
    "java": LanguageRules(
        mask=_C_FAMILY_MASK, top=JAVA_TOP, members=JAVA_MEMBERS,
        imports=re.compile(r"^(?:import|package)\b"),
        prefix=re.compile(r"^(?:@[\w.]+(?:\s*\([^()]*\))?\s*)+"),
    ),
    "csharp": LanguageRules(
        mask=_mask_pattern(_LINE_COMMENT, _BLOCK_COMMENT, _CS_VERBATIM, _DOUBLE_QUOTED, _CHAR_LITERAL),
        top=CS_TOP, members=CS_MEMBERS,
        imports=re.compile(r"^(?:global\s+)?using\s+(?:static\s+)?[\w.]+(?:\s*=\s*[^;]+)?;"),
        prefix=re.compile(r"^(?:\[[^\]]*\]\s*)+"),
    ),
    "c": _CPP, "cpp": _CPP,
    "go": LanguageRules(
        mask=_mask_pattern(_LINE_COMMENT, _BLOCK_COMMENT, _DOUBLE_QUOTED, _CHAR_LITERAL, _GO_RAW),
        top=GO_TOP, members=[], imports=re.compile(r"^(?:package|import)\b"),
    ),
    "rust": LanguageRules(
        mask=_mask_pattern(_LINE_COMMENT, _BLOCK_COMMENT, _RUST_RAW, _DOUBLE_QUOTED, _CHAR_LITERAL),
        top=RUST_TOP, members=RUST_MEMBERS,
        imports=re.compile(r"^(?:pub(?:\s*\([^)]*\))?\s+)?(?:use|extern\s+crate)\b"),
        prefix=re.compile(r"^(?:#\[[^\]]*\]\s*)+"),
    ),
}

SUPPORTED = frozenset(LANGUAGES)

_BRACES = re.compile(r"[{}]")
_PARENS = re.compile(r"[()]")
_MAX_DECLARATION_LINE = 500  # only short lines can be declarations (avoids slow regex on huge lines)
_MAX_SIGNATURE_CHARS = 3000  # how far to look for a body's opening brace
_CONTINUATION_END = (",", "(", "=", ":", "|", "&", "+", "-", "?", "=>", "->")
_CONTINUATION_START = ("{", ".", ":", "|", "&", "?", ")", ",", "=>", "->")
_CONTINUATION_WORDS = ("extends", "implements", "throws", "where", "const", "noexcept", "override", "final", "async")
_MERGED = object()


class _Scanner:
    def __init__(self, masked: str, rules: LanguageRules):
        self.text = masked
        self.rules = rules
        self.lines = masked.split("\n")
        self.line_starts = [0]
        for line in self.lines[:-1]:
            self.line_starts.append(self.line_starts[-1] + len(line) + 1)

    def line_of(self, offset: int) -> int:
        """0-based line index containing `offset`."""
        return bisect.bisect_right(self.line_starts, offset) - 1

    # -- scanning

    def scan(self, start: int, end: int, rules: list[Rule], allow_imports: bool, parent: str | None) -> list[CodeUnit]:
        units: list[CodeUnit] = []
        line = self.line_of(start)
        depth = 0
        while line < len(self.lines):
            line_start = max(self.line_starts[line], start)
            if line_start >= end:
                break
            line_end = min(self.line_starts[line] + len(self.lines[line]), end)
            segment = self.text[line_start:line_end]
            stripped = segment.lstrip()

            if depth == 0 and stripped:
                pos = line_start + (len(segment) - len(stripped))
                unit, resume = self._match(pos, stripped, end, rules, allow_imports, parent, units)
                if unit is not None:
                    if unit is not _MERGED:
                        units.append(unit)
                    line = resume
                    continue

            depth = max(0, depth + segment.count("{") - segment.count("}"))
            line += 1
        return units

    def _match(self, pos, stripped, end, rules, allow_imports, parent, units):
        shift = 0
        if self.rules.prefix:
            prefix = self.rules.prefix.match(stripped)
            if prefix:
                shift = prefix.end()
        candidate = stripped[shift : shift + _MAX_DECLARATION_LINE]

        if allow_imports and self.rules.imports and self.rules.imports.match(candidate):
            last = self.line_of(self._statement_end(pos, end))
            first = self.line_of(pos)
            previous = units[-1] if units else None
            if previous and previous.kind == "imports" and self._blank_between(previous.end_line, first):
                previous.end_line = last + 1
                return _MERGED, last + 1
            return CodeUnit("imports", None, first + 1, last + 1), last + 1

        for rule in rules:
            match = rule.pattern.match(candidate)
            if not match:
                continue
            need_arrow = rule.arrow and "function" not in match.group(0)
            # Search for the body right after the symbol name, so parameter
            # parentheses are seen in full (patterns may end inside them).
            body_search = match.end("name") if match.groupdict().get("name") else match.end()
            body = self._find_body(pos + shift + body_search, end, need_arrow)
            if body is None:
                continue
            open_at, close_at = body

            groups = match.groupdict()
            kind = rule.kind or KIND_BY_KEYWORD.get(groups.get("kw") or "", "class")
            name = groups.get("name")
            unit_parent = _receiver_type(groups["recv"]) if groups.get("recv") else parent
            last = self.line_of(close_at)
            unit = CodeUnit(kind, name, self.line_of(pos) + 1, last + 1, parent=unit_parent)

            if rule.container and self.text[open_at] == "{":
                is_namespace = kind in self.rules.namespace_kinds
                member_rules = self.rules.top if is_namespace else self.rules.members
                child_parent = name.split(" for ")[-1].strip() if (kind == "impl" and name) else name
                unit.children = self.scan(open_at + 1, close_at, member_rules, is_namespace, child_parent)
            return unit, last + 1
        return None, None

    # -- helpers

    def _find_body(self, pos: int, end: int, need_arrow: bool) -> tuple[int, int] | None:
        """Locate a declaration body starting after `pos`. Returns (open, close) offsets."""
        text = self.text
        limit = min(end, pos + _MAX_SIGNATURE_CHARS)
        depth = 0
        arrow_seen = not need_arrow
        i = pos
        while i < limit:
            c = text[i]
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
                if depth < 0:
                    return None
            elif depth == 0:
                if c == "{":
                    if not arrow_seen:
                        return None
                    close = self._match_close(i, end, _BRACES)
                    return (i, close) if close is not None else None
                if c in ";}":
                    return None
                if c == "=" and text.startswith("=>", i):
                    arrow_seen = True
                    j = i + 2
                    while j < end and text[j] in " \t\n":
                        j += 1
                    if j < end and text[j] == "(":
                        close = self._match_close(j, end, _PARENS)
                        return (j, close) if close is not None else None
                    if j < end and text[j] == "{":
                        i = j
                        continue
                    return (j, self._statement_end(j, end))  # expression-bodied arrow
                if c == "\n" and not self._continues(i, end):
                    return None
            i += 1
        return None

    def _match_close(self, open_at: int, end: int, pair: re.Pattern) -> int | None:
        opener = self.text[open_at]
        depth = 0
        for match in pair.finditer(self.text, open_at, end):
            depth += 1 if match.group() == opener else -1
            if depth == 0:
                return match.start()
        return None

    def _statement_end(self, pos: int, end: int) -> int:
        """Offset of the end of a statement: ';' or newline at bracket depth 0."""
        depth = 0
        i = pos
        while i < end:
            c = self.text[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth < 0:
                    return max(pos, i - 1)
            elif depth == 0 and c in ";\n":
                return i
            i += 1
        return max(pos, end - 1)

    def _continues(self, newline_at: int, end: int) -> bool:
        """Does a declaration signature continue onto the next line?"""
        line = self.line_of(newline_at)
        current = self.lines[line].rstrip()
        if current.endswith(_CONTINUATION_END):
            return True
        if line + 1 >= len(self.lines) or self.line_starts[line + 1] >= end:
            return False
        following = self.lines[line + 1].lstrip()
        return following.startswith(_CONTINUATION_START) or following.split(" ", 1)[0] in _CONTINUATION_WORDS

    def _blank_between(self, previous_end_line: int, next_start_index: int) -> bool:
        # previous_end_line is 1-based (== index of the line after it); next_start_index is 0-based.
        return all(not self.lines[i].strip() for i in range(previous_end_line, next_start_index))


def _receiver_type(receiver: str) -> str | None:
    """Go receiver "s *Server[T]" -> "Server"."""
    tokens = receiver.split()
    if not tokens:
        return None
    return tokens[-1].lstrip("*").split("[", 1)[0] or None


def parse_brace(text: str, language: str) -> list[CodeUnit]:
    rules = LANGUAGES[language]
    masked = rules.mask.sub(_blank, text)
    scanner = _Scanner(masked, rules)
    return scanner.scan(0, len(masked), rules.top, True, None)
