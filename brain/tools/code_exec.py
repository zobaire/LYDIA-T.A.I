"""Sandboxed Python code execution."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile

from brain.config import load_config


def run_python(code: str) -> str:
    """Execute Python code in a subprocess sandbox. Returns stdout/stderr."""
    cfg = load_config()
    timeout = cfg.get("tools", {}).get("code_exec", {}).get("timeout", 30)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        fname = f.name
    try:
        result = subprocess.run(
            [sys.executable, fname],
            capture_output=True, text=True, timeout=timeout,
        )
        output = result.stdout + result.stderr
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Code execution timed out after {timeout}s."
    except Exception as e:
        return f"Execution error: {e}"
    finally:
        try:
            os.unlink(fname)
        except OSError:
            pass
