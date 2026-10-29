"""File-type helpers: language detection and known binary extensions."""

from pathlib import PurePosixPath

EXTENSION_LANGUAGES = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "jsx",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".json": "json", ".jsonc": "json",
    ".md": "markdown", ".mdx": "markdown", ".rst": "restructuredtext", ".txt": "text",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss", ".sass": "sass", ".less": "less",
    ".vue": "vue", ".svelte": "svelte",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala", ".groovy": "groovy",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".m": "objective-c", ".mm": "objective-c", ".dart": "dart",
    ".lua": "lua", ".r": "r", ".jl": "julia", ".ex": "elixir", ".exs": "elixir", ".erl": "erlang",
    ".hs": "haskell", ".clj": "clojure", ".ml": "ocaml", ".fs": "fsharp", ".zig": "zig",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".fish": "shell", ".ps1": "powershell",
    ".bat": "batch", ".cmd": "batch",
    ".sql": "sql", ".graphql": "graphql", ".gql": "graphql", ".proto": "protobuf",
    ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".conf": "ini",
    ".xml": "xml", ".svg": "xml", ".csv": "csv", ".env": "dotenv",
    ".tf": "terraform", ".gradle": "groovy", ".lock": "text",
}

FILENAME_LANGUAGES = {
    "dockerfile": "dockerfile", "makefile": "makefile", "cmakelists.txt": "cmake",
    "gemfile": "ruby", "rakefile": "ruby", "procfile": "text", "license": "text",
    ".gitignore": "gitignore", ".dockerignore": "gitignore", ".npmignore": "gitignore",
    ".editorconfig": "ini", ".env.example": "dotenv",
}

# Extensions treated as binary without inspecting content.
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".avif", ".tif", ".tiff", ".psd",
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".class", ".pyc", ".wasm",
    ".sqlite", ".db", ".pkl", ".npy", ".pt", ".onnx",
}


def detect_language(path: str) -> str:
    name = PurePosixPath(path).name.lower()
    if name in FILENAME_LANGUAGES:
        return FILENAME_LANGUAGES[name]
    if name.startswith("dockerfile"):
        return "dockerfile"
    return EXTENSION_LANGUAGES.get(PurePosixPath(name).suffix, "text")


def has_binary_extension(path: str) -> bool:
    return PurePosixPath(path.lower()).suffix in BINARY_EXTENSIONS


def looks_binary(data: bytes) -> bool:
    """Heuristic used by git itself: a NUL byte near the start means binary."""
    return b"\x00" in data[:8000]
