"""File operations gated by an allowlist."""
from __future__ import annotations
import os
from pathlib import Path

from brain.config import load_config


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def _check_allowed(path: str) -> None:
    """Ensure target path is inside an allowed directory."""
    cfg = load_config()
    allowed = cfg.get("tools", {}).get("allowed_paths", ["~/Documents", "~/Desktop"])
    target = Path(path).expanduser().resolve()
    for a in allowed:
        allowed_dir = Path(a).expanduser().resolve()
        if target == allowed_dir or allowed_dir in target.parents:
            return
    raise PermissionError(f"Path not in allowed list: {path}")


def read_file(path: str) -> str:
    """Read a text file."""
    _check_allowed(path)
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_file(path: str, content: str) -> str:
    """Write text to a file."""
    _check_allowed(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Written to {path}."


def list_files(path: str) -> str:
    """List files in a directory."""
    _check_allowed(path)
    return "\n".join(os.listdir(path))


def apply_patch(path: str, patch: str) -> str:
    """Apply a unified-diff-style patch to a file.

    Patch format per hunk:
        <<<<<<< SEARCH
        old lines
        =======
        new lines
        >>>>>>> REPLACE
    """
    _check_allowed(path)
    if not Path(path).exists():
        raise FileNotFoundError(f"File not found: {path}")

    original = Path(path).read_text(encoding="utf-8")
    current = original

    hunks = patch.split("<<<<<<< SEARCH")
    for hunk in hunks[1:]:  # first element is before the first marker
        parts = hunk.split("=======")
        if len(parts) != 2:
            return "Patch malformed: missing ======= separator."
        old = parts[0].strip("\n")
        rest = parts[1].split(">>>>>>> REPLACE")
        if len(rest) != 2:
            return "Patch malformed: missing >>>>>>> REPLACE marker."
        new = rest[0].strip("\n")

        if old not in current:
            return f"Patch failed: search block not found in {path}."
        current = current.replace(old, new, 1)

    Path(path).write_text(current, encoding="utf-8")
    return f"Patched {path}."
