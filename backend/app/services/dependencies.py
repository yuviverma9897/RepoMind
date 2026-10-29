"""Import/dependency analysis from stored chunks: module graph, filtered graph views and impact analysis.

Supported: Python, JavaScript/TypeScript (incl. JSX/TSX) and Go. Every import is classified:
  resolved   - points to a file in the repository (relative imports, exact module paths)
  inferred   - matched to a repository file/package by path suffix (monorepo packages, Go module paths)
  external   - a third-party or standard-library package
  unresolved - looks internal but no file matched (path aliases, generated code, ...)
Static analysis only: dynamic imports, reflection, dependency injection and unsupported languages are not seen.
"""

import posixpath
import re
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

from app.db import database, store
from app.services import repo_code, secrets
from app.services.repo_code import AnalysisError

PYTHON = {"python"}
JS = {"javascript", "jsx", "typescript", "tsx"}
GO = {"go"}
SUPPORTED_LANGUAGES = PYTHON | JS | GO
# Programming languages whose imports are NOT analyzed (reported, never guessed).
CODE_LANGUAGES = {"java", "kotlin", "scala", "groovy", "c", "cpp", "csharp", "rust", "ruby", "php", "swift",
                  "objective-c", "dart", "lua", "r", "julia", "elixir", "erlang", "haskell", "clojure", "ocaml",
                  "fsharp", "zig", "shell", "powershell", "vue", "svelte"}
JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts", ".json")
MAX_GRAPH_NODES = 300
DEFAULT_GRAPH_NODES = 80
LIMITATIONS = [
    "Static import analysis only: dynamic imports, reflection, plugins and dependency injection are not detected.",
    "Supported languages: Python, JavaScript/TypeScript and Go. Other languages are listed as unsupported.",
    "\"Inferred\" links were matched by path suffix and may occasionally point to the wrong file.",
]

_PY_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+\(?([^\n#]*)", re.M)
_PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w.]+(?:[ \t]+as[ \t]+\w+)?(?:[ \t]*,[ \t]*[\w.]+(?:[ \t]+as[ \t]+\w+)?)*)", re.M)
_JS_IMPORT = re.compile(r"""(?:^|[;\s])(?:import|export)\s+(?:type\s+)?(?:[\w*{}\s,$]+?\s+from\s+)?['"]([^'"\n]+)['"]""", re.M)
_JS_FROM_LINE = re.compile(r"""^\s*\}?[\w\s,]*\}?\s*from\s+['"]([^'"\n]+)['"]""", re.M)  # end of multi-line import
_JS_REQUIRE = re.compile(r"""\b(?:require|import)\s*\(\s*['"]([^'"\n]+)['"]\s*\)""")
_GO_SINGLE = re.compile(r'^[ \t]*import[ \t]+(?:[\w.]+[ \t]+)?"([^"]+)"', re.M)
_GO_BLOCK = re.compile(r"^[ \t]*import[ \t]*\((.*?)\)", re.M | re.S)
_GO_BLOCK_LINE = re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"', re.M)


@dataclass
class ImportRef:
    file: str
    line: int
    specifier: str
    status: str  # resolved | inferred | external | unresolved
    target: str | None  # repository file or package directory (Go), or external package name


@dataclass
class ModuleGraph:
    repository_id: int
    repository: str
    built_at: float
    build_ms: int
    files: dict[str, str]  # analyzed file -> language
    imports: list[ImportRef]
    edges: dict[str, dict[str, int]] = field(default_factory=dict)  # file -> {target: count}
    reverse: dict[str, dict[str, list[int]]] = field(default_factory=dict)  # target -> {importer: [lines]}
    unsupported: dict[str, int] = field(default_factory=dict)  # language -> file count
    go_packages: dict[str, list[str]] = field(default_factory=dict)  # package dir -> files

    def stats(self) -> dict:
        counts = Counter(i.status for i in self.imports)
        return {"files_analyzed": len(self.files), "imports": len(self.imports),
                "resolved": counts["resolved"], "inferred": counts["inferred"], "external": counts["external"],
                "unresolved": counts["unresolved"], "unsupported_languages": self.unsupported,
                "build_ms": self.build_ms}


# ---------------------------------------------------------------- extraction

def extract_imports(content: str, language: str) -> list[tuple[int, str, list[str]]]:
    """[(line offset within content, specifier, imported names)] for one chunk/file."""
    found = []

    def line_of(pos: int) -> int:
        return content.count("\n", 0, pos)

    if language in PYTHON:
        for m in _PY_FROM.finditer(content):
            names = [n.split(" as ")[0].strip() for n in m.group(3).replace(")", "").split(",")]
            found.append((line_of(m.start()), m.group(1) + m.group(2), [n for n in names if n.isidentifier()]))
        for m in _PY_IMPORT.finditer(content):
            for part in m.group(1).split(","):
                found.append((line_of(m.start()), part.split(" as ")[0].strip(), []))
    elif language in JS:
        seen = set()
        for pattern in (_JS_IMPORT, _JS_FROM_LINE, _JS_REQUIRE):
            for m in pattern.finditer(content):
                line = line_of(m.start(1))
                if (line, m.group(1)) not in seen:
                    seen.add((line, m.group(1)))
                    found.append((line, m.group(1), []))
    elif language in GO:
        for m in _GO_SINGLE.finditer(content):
            found.append((line_of(m.start()), m.group(1), []))
        for block in _GO_BLOCK.finditer(content):
            for m in _GO_BLOCK_LINE.finditer(block.group(1)):
                found.append((line_of(block.start(1) + m.start(1)), m.group(1), []))
    return found


# ---------------------------------------------------------------- resolution

class _Resolver:
    def __init__(self, files: dict[str, str]):
        self.files = files
        self.py_modules: dict[str, list[str]] = defaultdict(list)  # dotted suffix -> files
        self.js_suffixes: dict[str, list[str]] = defaultdict(list)  # path suffix without extension -> files
        self.go_dirs: dict[str, list[str]] = defaultdict(list)  # package dir -> files
        for path, language in files.items():
            if language in PYTHON:
                parts = path[:-3].split("/") if path.endswith(".py") else path.rsplit(".", 1)[0].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                for i in range(len(parts)):
                    self.py_modules[".".join(parts[i:])].append(path)
            elif language in JS:
                stem = _strip_js_ext(path)
                parts = stem.split("/")
                if parts[-1] == "index" and len(parts) > 1:
                    parts = parts[:-1]
                for i in range(1, len(parts)):
                    self.js_suffixes["/".join(parts[i:])].append(path)
            elif language in GO:
                self.go_dirs[posixpath.dirname(path)].append(path)
        self.go_module: tuple[str, str] | None = None
        self.go_suffix: dict[str, list[str]] = defaultdict(list)
        for directory in self.go_dirs:
            parts = directory.split("/") if directory else []
            for i in range(len(parts)):
                self.go_suffix["/".join(parts[i:])].append(directory)

    def resolve(self, importer: str, language: str, spec: str, names: list[str]) -> list[tuple[str, str | None]]:
        if language in PYTHON:
            return self._python(importer, spec, names)
        if language in JS:
            return [self._js(importer, spec)]
        return [self._go(spec)]

    def _python(self, importer, spec, names):
        level = len(spec) - len(spec.lstrip("."))
        module = spec[level:]
        if level:
            base = posixpath.dirname(importer).split("/") if posixpath.dirname(importer) else []
            base = base[:len(base) - (level - 1)] if level > 1 else base
            root = "/".join(base + (module.split(".") if module else []))
            hits = [f for f in (f"{root}/{n}.py" for n in names) if f in self.files]  # from . import views
            target = next((f for f in (f"{root}.py", f"{root}/__init__.py") if f in self.files), None)
            if hits:
                return [("resolved", h) for h in hits]
            return [("resolved", target)] if target else [("unresolved", None)]
        submodules = [f"{module}.{n}" for n in names if f"{module}.{n}" in self.py_modules]
        candidates = submodules or [module]
        results = []
        for name in candidates:
            files = self.py_modules.get(name)
            if files:
                results.append(("resolved" if len(files) == 1 else "inferred", _closest(importer, files)))
        return results or [("external", module.split(".")[0])]

    def _js(self, importer, spec):
        if spec.startswith((".", "/")):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec)).lstrip("/")
            for candidate in [base] + [base + e for e in JS_EXTENSIONS] + [f"{base}/index{e}" for e in JS_EXTENSIONS]:
                if candidate in self.files:
                    return ("resolved", candidate)
            return ("unresolved", None)
        if spec.startswith(("@/", "~/", "#")):
            files = self.js_suffixes.get(_strip_js_ext(spec[2:] if spec[1] == "/" else spec[1:]))
            return ("inferred", _closest(importer, files)) if files else ("unresolved", None)
        files = self.js_suffixes.get(_strip_js_ext(spec))  # monorepo packages, e.g. "shared/ReactTypes"
        if files:
            return ("inferred", _closest(importer, files))
        package = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
        return ("external", package)

    def infer_go_module(self, specs: list[str]) -> None:
        """Infer (module path, module root directory) from imports whose tail matches a package directory.

        go.mod files are not indexed, and the module may live in a subdirectory (monorepos).
        """
        votes = Counter()
        for spec in specs:
            parts = spec.split("/")
            if "." not in parts[0]:
                continue
            for i in range(1, len(parts)):
                suffix = "/".join(parts[i:])
                dirs = self.go_suffix.get(suffix)
                if dirs and len(dirs) == 1:
                    root = dirs[0][: -len(suffix)].rstrip("/")
                    votes[("/".join(parts[:i]), root)] += 1
                    break
        self.go_module = votes.most_common(1)[0][0] if votes else None

    def _go(self, spec):
        parts = spec.split("/")
        if "." not in parts[0]:
            return ("external", spec)  # standard library ("net/http")
        if self.go_module:
            module, root = self.go_module
            if spec == module or spec.startswith(module + "/"):
                directory = "/".join(p for p in (root, spec[len(module):].strip("/")) if p)
                if directory in self.go_dirs:
                    return ("inferred", directory + "/" if directory else "/")
                return ("unresolved", None)
        return ("external", "/".join(parts[:3]))


def _strip_js_ext(path: str) -> str:
    for ext in sorted(JS_EXTENSIONS, key=len, reverse=True):
        if path.endswith(ext):
            return path[: -len(ext)]
    return path


def _closest(importer: str, files: list[str]) -> str:
    """Among candidate files, prefer the one sharing the longest directory prefix with the importer."""
    def shared(path):
        a, b = importer.split("/"), path.split("/")
        n = 0
        while n < min(len(a), len(b)) - 1 and a[n] == b[n]:
            n += 1
        return (n, -len(b))
    return max(files, key=shared)


# ---------------------------------------------------------------- graph building + cache

_cache: dict[int, tuple[object, ModuleGraph]] = {}
_lock = threading.Lock()


def get_graph(repository_id: int) -> ModuleGraph:
    """Module graph for a repository, cached until the repository is re-indexed."""
    with database.session_scope() as session:
        repository = repo_code.require_repository(session, repository_id)
        version = (repository.indexed_at, repository.indexed_commit_sha)
        name = repository.full_name
    with _lock:
        cached = _cache.get(repository_id)
        if cached and cached[0] == version:
            return cached[1]
    graph = build_graph(repository_id, name)
    with _lock:
        _cache[repository_id] = (version, graph)
    return graph


def build_graph(repository_id: int, repository: str) -> ModuleGraph:
    started = time.monotonic()
    with database.session_scope() as session:
        languages = store.file_languages(session, repository_id)
        files = {p: lang for p, lang in languages.items() if lang in SUPPORTED_LANGUAGES}
        unsupported = Counter(lang for lang in languages.values() if lang in CODE_LANGUAGES)
        raw = []
        seen = set()
        for row in store.iter_chunks(session, repository_id, languages=SUPPORTED_LANGUAGES):
            content = row.content
            if "import" not in content and "require" not in content and "from" not in content:
                continue
            for offset, spec, names in extract_imports(content, row.language):
                key = (row.file_path, row.start_line + offset, spec)
                if key not in seen:  # overlapping chunk parts repeat lines
                    seen.add(key)
                    raw.append((row.file_path, row.language, row.start_line + offset, spec, names))
    resolver = _Resolver(files)
    resolver.infer_go_module([spec for _, language, _, spec, _ in raw if language in GO])
    graph = ModuleGraph(repository_id, repository, time.time(), 0, files, [], unsupported=dict(unsupported),
                        go_packages={d + "/" if d else "/": f for d, f in resolver.go_dirs.items()})
    for path, language, line, spec, names in raw:
        for status, target in resolver.resolve(path, language, spec, names):
            graph.imports.append(ImportRef(path, line, spec, status, target))
            if status in ("resolved", "inferred") and target and target != path:
                graph.edges.setdefault(path, {})
                graph.edges[path][target] = graph.edges[path].get(target, 0) + 1
                graph.reverse.setdefault(target, {}).setdefault(path, []).append(line)
    graph.build_ms = round((time.monotonic() - started) * 1000)
    return graph


# ---------------------------------------------------------------- graph view

def graph_view(repository_id: int, *, level: str = "directory", depth: int = 2, path_prefix: str | None = None,
               max_nodes: int = DEFAULT_GRAPH_NODES, include_external: bool = False) -> dict:
    if level not in ("directory", "file"):
        raise AnalysisError(400, "invalid_level", "level must be 'directory' or 'file'.")
    if not 1 <= max_nodes <= MAX_GRAPH_NODES:
        raise AnalysisError(400, "invalid_max_nodes", f"max_nodes must be between 1 and {MAX_GRAPH_NODES}.")
    if not 1 <= depth <= 6:
        raise AnalysisError(400, "invalid_depth", "depth must be between 1 and 6.")
    graph = get_graph(repository_id)
    prefix = (path_prefix or "").strip().lstrip("/")

    def key(path: str) -> str:
        if level == "file":
            return path
        directory = path.rstrip("/").rsplit("/", 1)[0] if "/" in path.rstrip("/") else ""
        if path.endswith("/"):  # Go package directory
            directory = path.rstrip("/")
        return "/".join(directory.split("/")[:depth]) + "/" if directory else "/"

    def inside(path: str) -> bool:
        return not prefix or path.startswith(prefix)

    weights: Counter = Counter()
    node_files: dict[str, set] = defaultdict(set)
    for path, language in graph.files.items():
        if inside(path):
            node_files[key(path)].add(path)
    for source, targets in graph.edges.items():
        if not inside(source):
            continue
        for target, count in targets.items():
            if not inside(target):
                continue
            a, b = key(source), key(target)
            if a != b:
                weights[(a, b)] += count
    external: Counter = Counter()
    if include_external:
        for ref in graph.imports:
            if ref.status == "external" and inside(ref.file):
                external[(key(ref.file), "pkg:" + ref.target)] += 1

    degree: Counter = Counter()
    for (a, b), w in list(weights.items()) + list(external.items()):
        degree[a] += w
        degree[b] += w
    internal_ids = set(node_files) | {a for a, _ in weights} | {b for _, b in weights}
    total_nodes = len(internal_ids)
    ranked = sorted(internal_ids, key=lambda n: (-degree[n], n))
    external_budget = min(15, max_nodes // 4) if include_external else 0
    kept = set(ranked[: max_nodes - external_budget if external else max_nodes])
    if external:
        top_packages = Counter()
        for (a, pkg), w in external.items():
            if a in kept:
                top_packages[pkg] += w
        kept |= {p for p, _ in top_packages.most_common(external_budget)}

    edges = [{"source": a, "target": b, "type": "imports", "weight": w} for (a, b), w in list(weights.items()) + list(external.items())
             if a in kept and b in kept]
    in_deg, out_deg = Counter(), Counter()
    for e in edges:
        out_deg[e["source"]] += e["weight"]
        in_deg[e["target"]] += e["weight"]
    nodes = []
    for node in sorted(kept, key=lambda n: (-degree[n], n)):
        if node.startswith("pkg:"):
            nodes.append({"id": node, "label": node[4:], "type": "external", "language": None, "files": 0,
                          "in_degree": in_deg[node], "out_degree": out_deg[node]})
            continue
        files = node_files.get(node, set())
        is_go_package = level == "file" and node.endswith("/")
        langs = Counter(graph.files[f] for f in files) if files else Counter()
        nodes.append({"id": node, "label": node.rstrip("/").rsplit("/", 1)[-1] or "/" if level == "file" else node,
                      "type": "package" if is_go_package else level, "language": langs.most_common(1)[0][0] if langs else
                      ("go" if is_go_package else None), "files": len(files) or len(graph.go_packages.get(node, [])),
                      "in_degree": in_deg[node], "out_degree": out_deg[node],
                      "file": node if level == "file" and not is_go_package else None})
    return {
        "repository_id": repository_id, "level": level, "depth": depth, "path_prefix": prefix or None,
        "nodes": nodes, "edges": sorted(edges, key=lambda e: -e["weight"]),
        "total_nodes": total_nodes, "total_edges": len(weights),
        "truncated": total_nodes > sum(not n.startswith("pkg:") for n in kept),
        "stats": graph.stats(), "limitations": LIMITATIONS,
    }


# ---------------------------------------------------------------- impact analysis

MAX_DEPENDENTS = 100
MAX_REFERENCES = 50


def impact(repository_id: int, file_path: str, symbol: str | None = None, depth: int = 2) -> dict:
    """Deterministic part of impact analysis: who imports the file (transitively) and where a symbol is named."""
    if not 1 <= depth <= 3:
        raise AnalysisError(400, "invalid_depth", "depth must be between 1 and 3.")
    if symbol is not None and not re.fullmatch(r"[A-Za-z_$][\w$]{0,199}", symbol):
        raise AnalysisError(400, "invalid_symbol", "symbol must be a plain identifier (e.g. login_required).")
    graph = get_graph(repository_id)
    file = repo_code.load_file(repository_id, file_path)  # 404 if the file is not indexed
    if symbol and not any(s["name"] == symbol for s in file["symbols"]):
        raise AnalysisError(404, "symbol_not_found", f"No symbol named '{symbol}' in {file_path}.")
    targets = [file_path]
    package = posixpath.dirname(file_path) + "/" if file["language"] in GO else None
    if package is not None:
        targets.append(package if package != "/" else "/")

    direct = []
    for target in targets:
        for importer, lines in sorted(graph.reverse.get(target, {}).items()):
            if importer != file_path:
                direct.append({"file_path": importer, "lines": sorted(set(lines)), "distance": 1})
    transitive, seen = [], {file_path} | {d["file_path"] for d in direct}
    queue = deque((d["file_path"], 1) for d in direct)
    while queue and len(transitive) < MAX_DEPENDENTS:
        current, dist = queue.popleft()
        if dist >= depth:
            continue
        for importer, lines in graph.reverse.get(current, {}).items():
            if importer not in seen:
                seen.add(importer)
                transitive.append({"file_path": importer, "lines": sorted(set(lines)), "distance": dist + 1, "via": current})
                queue.append((importer, dist + 1))

    dependencies = [{"specifier": r.specifier, "status": r.status, "target": r.target, "line": r.line}
                    for r in graph.imports if r.file == file_path]
    references = _symbol_references(repository_id, symbol, file_path, file, {d["file_path"] for d in direct}) if symbol else []
    supported = file["language"] in SUPPORTED_LANGUAGES
    return {
        "repository_id": repository_id, "file_path": file_path, "language": file["language"], "symbol": symbol,
        "depth": depth, "imports_supported": supported,
        "direct_dependents": direct[:MAX_DEPENDENTS], "transitive_dependents": transitive,
        "dependencies": dependencies, "references": references,
        "truncated": len(direct) > MAX_DEPENDENTS or len(transitive) >= MAX_DEPENDENTS,
        "limitations": LIMITATIONS + ([f"Imports of {file['language']} files are not analyzed, so dependents may be missing."]
                                      if not supported else []) +
                       ["Symbol references are text matches of the name: they can include unrelated code with the same name "
                        "and miss indirect uses."] * bool(symbol),
    }


def _symbol_references(repository_id, symbol, file_path, file, importers) -> list[dict]:
    definition = next(s for s in file["symbols"] if s["name"] == symbol)
    pattern = re.compile(rf"(?<![\w$]){re.escape(symbol)}(?![\w$])")
    found, seen = [], set()
    with database.session_scope() as session:
        rows = store.chunks_containing(session, repository_id, symbol, limit=400)
    for row in rows:
        for offset, text in enumerate(row.content.split("\n")):
            line = row.start_line + offset
            if not pattern.search(text) or (row.file_path, line) in seen:
                continue
            if row.file_path == file_path and definition["start_line"] <= line <= definition["end_line"]:
                continue  # the definition itself
            seen.add((row.file_path, line))
            found.append({"file_path": row.file_path, "line": line, "text": secrets.mask(text.strip())[:200],
                          "same_file": row.file_path == file_path, "file_imports_target": row.file_path in importers})
            if len(found) >= MAX_REFERENCES:
                return found
    return found
