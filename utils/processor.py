"""
Recursive file walker with filtering and intelligent context trimming.
Prioritizes key files; truncates content when exceeding token budget.
"""

from pathlib import Path
from typing import NamedTuple, Optional

try:
    import tiktoken
    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENCODER = None

MAX_TOKENS = 6_000
PRIORITY_FILES = frozenset({
    "README.md", "README.MD", "readme.md", "README.rst", "README.txt",
    "LICENSE", "LICENSE.md", "LICENSE.txt",
    "package.json", "pyproject.toml", "requirements.txt",
    "main.py", "app.py", "app.js", "index.js", "index.ts", "main.ts",
})
CONFIG_FILES = frozenset({
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "Pipfile", "Cargo.toml", "go.mod", "Makefile", "Dockerfile", "dockerfile",
    "tsconfig.json", "vue.config.js", "vite.config.js", "vite.config.ts",
})
IGNORE_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env", ".env",
    "dist", "build", ".next", ".nuxt", ".cache", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "vendor", "bower_components",
})
IGNORE_FILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "composer.lock", "Cargo.lock", "go.sum",
})
BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp",
    ".pdf", ".zip", ".tar", ".gz", ".exe", ".so", ".dll", ".dylib",
    ".woff", ".woff2", ".ttf", ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".pyc", ".pyo", ".class", ".o", ".obj",
})


class ProcessedRepo(NamedTuple):
    directory_tree: str
    file_contents: str


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken (cl100k_base). Fallback: chars/4 if tiktoken unavailable."""
    if _ENCODER:
        return len(_ENCODER.encode(text))
    return (len(text) + 3) // 4


def _should_skip(path: Path, repo_root: Path, is_dir: bool) -> bool:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return True
    parts = rel.parts
    if is_dir:
        return parts[-1].lower() in IGNORE_DIRS or parts[-1].startswith(".")
    if path.name in IGNORE_FILES or path.name.startswith("."):
        return True
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    return any(p.startswith(".") or p.lower() in IGNORE_DIRS for p in parts[:-1])


def _build_tree(root: Path, repo_root: Path, prefix: str, depth: int, max_depth: int) -> str:
    if depth >= max_depth:
        return ""
    lines = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return ""
    dirs = [e for e in entries if e.is_dir() and not _should_skip(e, repo_root, True)]
    files = [e for e in entries if e.is_file() and not _should_skip(e, repo_root, False)]
    for i, e in enumerate(dirs):
        is_last = i == len(dirs) - 1 and not files
        lines.append(prefix + ("└── " if is_last else "├── ") + e.name + "/")
        add = "    " if is_last else "│   "
        lines.append(_build_tree(e, repo_root, prefix + add, depth + 1, max_depth))
    for i, e in enumerate(files):
        lines.append(prefix + ("└── " if i == len(files) - 1 else "├── ") + e.name)
    return "\n".join(lines)


def _read_file(path: Path, max_chars: int = 50_000) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_chars)
    except (OSError, UnicodeDecodeError):
        return None


def _truncate_middle_out(content: str, max_tokens: int) -> str:
    """
    Middle-out truncation: keep top (imports, headers, class/function defs) and bottom (exports, main logic).
    For long files (e.g. 2000-line utils.py), this preserves the most informative parts.
    """
    total = count_tokens(content)
    if total <= max_tokens:
        return content
    head_budget = max_tokens // 2
    tail_budget = max_tokens - head_budget
    approx_chars_per_token = max(1, len(content) // total)
    head_chars = int(head_budget * approx_chars_per_token)
    tail_chars = int(tail_budget * approx_chars_per_token)
    head = content[:head_chars].rstrip()
    tail = content[-tail_chars:].lstrip() if tail_chars < len(content) else ""
    return f"{head}\n\n... [truncated {total - max_tokens} tokens] ...\n\n{tail}"


def _priority_score(path: Path, repo_root: Path) -> int:
    """Higher = more important. Priority files first, then configs, then by path depth."""
    name = path.name
    try:
        rel = path.relative_to(repo_root)
        depth = len(rel.parts)
    except ValueError:
        depth = 0
    if name in PRIORITY_FILES:
        return 1000 - depth
    if name in CONFIG_FILES:
        return 500 - depth
    return 100 - depth


def _collect_files(repo_root: Path) -> list[tuple[Path, str, int]]:
    """Recursive walk; return (path, content, priority)."""
    collected: list[tuple[Path, str, int]] = []

    def walk(dir_path: Path) -> None:
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if _should_skip(entry, repo_root, entry.is_dir()):
                continue
            if entry.is_dir():
                walk(entry)
                continue
            content = _read_file(entry)
            if content:
                score = _priority_score(entry, repo_root)
                collected.append((entry, content, score))

    walk(repo_root)
    return collected


def process_repo(repo_root: Path) -> ProcessedRepo:
    """
    Build directory tree and prioritized file contents.
    If total exceeds MAX_TOKENS, applies intelligent truncation (keep head + tail).
    """
    tree = _build_tree(repo_root, repo_root, "", 0, 5)
    tree_str = f"Directory Tree:\n```\n{tree}\n```"
    tree_tokens = count_tokens(tree_str)
    budget = max(0, MAX_TOKENS - tree_tokens - 200)

    collected = _collect_files(repo_root)
    collected.sort(key=lambda x: -x[2])

    parts: list[str] = []
    used = 0

    for path, content, _ in collected:
        if used >= budget:
            break
        rel = path.relative_to(repo_root)
        header = f"\n--- {rel} ---\n"
        header_tokens = count_tokens(header)
        remaining = budget - used - header_tokens
        if remaining <= 0:
            break
        content_tokens = count_tokens(content)
        if content_tokens > remaining:
            content = _truncate_middle_out(content, remaining)
        parts.append(header + content)
        used += header_tokens + count_tokens(content)

    file_contents = "\n".join(parts) if parts else "(No readable files)"
    return ProcessedRepo(directory_tree=tree_str, file_contents=file_contents)
