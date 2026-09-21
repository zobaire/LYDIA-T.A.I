"""Rolling conversation context with auto-summarization."""
from __future__ import annotations
from collections import deque
from pathlib import Path

from brain.config import load_config
from brain.llm import simple_chat


class ContextManager:
    """Keeps the last N messages and auto-summarizes older ones."""

    def __init__(
        self,
        max_messages: int | None = None,
        summarize_after: int | None = None,
        initial_summaries: list[str] | None = None,
        save_summary_cb: Callable[[str], None] | None = None,
    ):
        cfg = load_config().get("context", {})
        self.max_messages = max_messages if max_messages is not None else cfg.get("max_messages", 24)
        self.summarize_after = (
            summarize_after if summarize_after is not None else cfg.get("summarize_after", 16)
        )
        if self.summarize_after > self.max_messages:
            raise ValueError(
                f"summarize_after ({self.summarize_after}) must be <= max_messages ({self.max_messages})"
            )
        self._window: deque[dict] = deque(maxlen=self.max_messages)
        self._summary: str | None = None
        # Optional callback to persist a generated summary (e.g. to SQLite)
        # so deep-task context survives restarts.
        self._save_summary_cb = save_summary_cb
        # Summaries persisted by a previous session (SQLite), replayed into
        # the prompt so conversation context survives restarts.
        self._prior_summaries: list[str] = list(initial_summaries or [])

    def add(self, role: str, content: str) -> None:
        """Add a message. Auto-summarizes oldest messages when window fills."""
        self._window.append({"role": role, "content": content})
        if len(self._window) >= self.summarize_after:
            self._compress_oldest()

    def _compress_oldest(self) -> None:
        """Summarize and drop the oldest half of the window."""
        half = max(1, len(self._window) // 2)
        to_compress = [self._window.popleft() for _ in range(half)]
        try:
            text = "\n".join(f"{m['role']}: {m['content']}" for m in to_compress)
            prompt = f"Summarize this conversation in 2-3 sentences:\n{text}"
            summary = simple_chat([{"role": "user", "content": prompt}])
            summary = (summary or "").strip()
            # Never inject provider error text into context. We've already
            # dropped the oldest messages either way — a failed summary just
            # means we carry on without one.
            if not summary or summary.startswith("[LLM error"):
                self._summary = None
                return
            self._summary = f"[Earlier context: {summary}]"
            if self._save_summary_cb:
                try:
                    self._save_summary_cb(summary)
                except Exception:
                    pass
        except Exception:
            # Summarization failed (timeout, provider down…). Oldest messages
            # are already dropped; continue without a summary.
            self._summary = None

    def get_messages(self) -> list[dict]:
        """Return context window, prefixed with current + prior summaries."""
        messages = list(self._window)
        if self._summary:
            messages = [{"role": "system", "content": self._summary}] + messages
        if self._prior_summaries:
            prior = [
                {"role": "system", "content": f"[Earlier session: {s}]"}
                for s in self._prior_summaries
            ]
            messages = prior + messages
        return messages

    def clear(self) -> None:
        self._window.clear()
        self._summary = None
