"""Project introspection and code-editing tools for Lydia's coding brain."""
from __future__ import annotations
import fnmatch
import os
from pathlib import Path

from brain.tools.file_ops import apply_patch, read_file


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def _is_ignored(path: Path, ignore_patterns: list[str]) -> bool:
    rel = path.relative_to(_repo_root())
    parts = rel.parts
    for part in parts:
        for pattern in ignore_patterns:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def _default_ignore() -> list[str]:
    patterns = [
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        "*.pyc", "*.pyo", ".DS_Store", "Thumbs.db",
    ]
    gitignore = _repo_root() / ".gitignore"
    if gitignore.exists():
        for line in gitignore.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line.rstrip("/"))
    return patterns


def read_project(root: str = ".", depth: int = 4) -> str:
    """Return a concise tree of the project up to a given depth."""
    base = _repo_root() if root == "." else Path(root).resolve()
    if not base.exists():
        return f"Path not found: {base}"

    ignore = _default_ignore()
    lines: list[str] = [str(base.name) + "/"]

    def walk(current: Path, prefix: str, current_depth: int):
        if current_depth >= depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return
        for i, entry in enumerate(entries):
            if _is_ignored(entry, ignore):
                continue
            is_last = i == len(entries) - 1
            branch = "└── " if is_last else "├── "
            lines.append(f"{prefix}{branch}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                extension = "    " if is_last else "│   "
                walk(entry, prefix + extension, current_depth + 1)

    walk(base, "", 0)
    return "\n".join(lines)


def grep_project(pattern: str, root: str = ".", max_results: int = 20) -> str:
    """Grep project files for a regex or substring. Returns matching lines."""
    import re
    base = _repo_root() if root == "." else Path(root).resolve()
    ignore = _default_ignore()
    matches: list[str] = []

    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if _is_ignored(path, ignore):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern in line or re.search(pattern, line):
                rel = path.relative_to(base)
                matches.append(f"{rel}:{i}: {line.strip()}")
                if len(matches) >= max_results:
                    return "\n".join(matches)
    if not matches:
        return f"No matches for '{pattern}'."
    return "\n".join(matches)


def read_symbols(file_path: str) -> str:
    """Extract function/class definitions from a Python or JS/TS file."""
    import re
    try:
        text = read_file(file_path)
    except Exception as e:
        return f"Could not read file: {e}"

    lines = []
    if file_path.endswith(".py"):
        for match in re.finditer(r"^(class|def)\s+(\w+)\s*[\(:\)]", text, re.MULTILINE):
            lines.append(f"{match.group(1)} {match.group(2)}")
    elif file_path.endswith((".js", ".ts", ".jsx", ".tsx")):
        for match in re.finditer(
            r"^(export\s+)?(class|function|const|let|var)\s+(\w+)", text, re.MULTILINE
        ):
            lines.append(f"{match.group(2)} {match.group(3)}")
    else:
        return "Symbol reading only supported for Python and JS/TS files."

    if not lines:
        return "No symbols found."
    return "\n".join(lines)


def run_shell(command: str, timeout: int = 30) -> str:
    """Run a shell command and return stdout/stderr."""
    import subprocess
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout + result.stderr
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Shell command timed out after {timeout}s."
    except Exception as e:
        return f"Shell error: {e}"


def run_tests(path: str = ".") -> str:
    """Attempt to run the project's test suite."""
    root = _repo_root() if path == "." else Path(path).resolve()
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        return run_shell("pytest", timeout=60)
    if (root / "package.json").exists():
        return run_shell("npm test", timeout=60)
    return "No recognized test runner (pytest / npm test) found."


def run_linter(path: str = ".") -> str:
    """Attempt to run a linter on the project."""
    root = _repo_root() if path == "." else Path(path).resolve()
    if (root / "pyproject.toml").exists():
        return run_shell("ruff check . || flake8 .", timeout=60)
    if (root / "package.json").exists():
        return run_shell("npx eslint .", timeout=60)
    return "No recognized linter config found."


def edit_file(path: str, patch: str) -> str:
    """Apply a safe patch to a file."""
    return apply_patch(path, patch)
