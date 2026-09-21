"""Long-term memory: ChromaDB semantic facts + SQLite conversation summaries."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import chromadb
from sqlalchemy import create_engine, text

from brain.config import load_config


_FACT_TRIGGERS = [
    "my name is", "i am", "i'm", "i work", "i like", "i hate",
    "i love", "i prefer", "i need", "i want", "i use", "i have",
    "i live", "i go to", "i study", "i play", "i own",
    "my favorite", "my email", "my phone", "my address",
    "my birthday", "my password", "my project", "my computer",
    "remember that", "remember this", "don't forget", "keep in mind",
    "call me", "i usually", "i always", "i never",
]

# Phrases that match a trigger ("i have...", "i want...") but aren't real
# facts — "i have a question" is not a memory, it's small talk. If the user
# message is (or leads with) one of these, don't store it.
_FACT_JUNK_PATTERNS = [
    "i have a question", "i have a few questions", "i have a quick question",
    "i have another question", "i have to ask", "i have to", "i have a doubt",
    "i want to ask", "i want to know", "i want to ask you", "i'd like to ask",
    "i would like to ask", "i want to", "i'd like to", "i would like to",
    "i need help", "i need your help", "i need to ask", "i need a", "i need an",
    "i need to", "i need you to", "can i ask", "can i have", "can i get",
    "could you", "can you", "will you", "i'm wondering", "i am wondering",
    "i was wondering", "i'm curious", "i am curious", "i'm just curious",
    "i think", "i feel like", "i feel that", "i'm trying to", "i am trying to",
    "i'm just", "i am just", "i was just", "i don't know", "i dont know",
    "i'm not sure", "i am not sure", "i'm asking", "i am asking",
    "i'm going to", "i am going to", "i'm looking for", "i am looking for",
    "i'm having", "i am having", "i'm getting", "i am getting",
]

# Facts shorter than this are almost always fragments, not memories.
_FACT_MIN_LENGTH = 12


def _looks_like_fact(text: str) -> bool:
    """True when a user message reads like a storable personal fact."""
    lowered = text.strip().lower()
    if not lowered or lowered.endswith("?"):
        return False
    if len(lowered) < _FACT_MIN_LENGTH:
        return False
    if any(p in lowered for p in _FACT_JUNK_PATTERNS):
        return False
    return any(t in lowered for t in _FACT_TRIGGERS)


def _repo_root() -> Path:
    return Path(__file__).parent.parent


class Memory:
    """Semantic fact store + conversation summary store."""

    def __init__(
        self,
        db_path: str | None = None,
        chroma_path: str | None = None,
        top_k: int | None = None,
    ):
        cfg = load_config().get("memory", {})
        self._top_k = top_k if top_k is not None else cfg.get("top_k", 3)

        db_path = db_path or str(_repo_root() / cfg.get("db_path", "memory_data/memory.db"))
        chroma_path = chroma_path or str(_repo_root() / cfg.get("chroma_path", "memory_data/chroma"))

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(chroma_path).mkdir(parents=True, exist_ok=True)

        self._engine = create_engine(f"sqlite:///{db_path}")
        self._chroma = chromadb.PersistentClient(path=chroma_path)
        self._facts = self._chroma.get_or_create_collection("facts")
        self._setup_db()

    def _setup_db(self) -> None:
        with self._engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS summaries "
                "(id INTEGER PRIMARY KEY, content TEXT, created_at TEXT)"
            ))
            conn.commit()

    def store_fact(self, fact: str) -> None:
        """Store a semantic fact. Identical facts are deduplicated by hash ID."""
        fact_id = hashlib.md5(fact.encode()).hexdigest()
        self._facts.upsert(ids=[fact_id], documents=[fact])

    def search_facts(self, query: str, top_k: int | None = None) -> list[str]:
        """Return semantically relevant facts for the query."""
        k = top_k if top_k is not None else self._top_k
        count = self._facts.count()
        if count == 0:
            return []
        results = self._facts.query(query_texts=[query], n_results=min(k, count))
        return [d for d in results.get("documents", [[]])[0] if d]

    def save_conversation_summary(self, summary: str) -> None:
        with self._engine.connect() as conn:
            conn.execute(
                text("INSERT INTO summaries (content, created_at) VALUES (:c, :t)"),
                {"c": summary, "t": datetime.now(timezone.utc).isoformat()},
            )
            conn.commit()

    def get_recent_summaries(self, n: int = 5) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text("SELECT content FROM summaries ORDER BY id DESC LIMIT :n"),
                {"n": n},
            ).fetchall()
        return [r[0] for r in rows]

    def extract_and_store_facts(self, assistant_response: str, user_message: str) -> None:
        """Store user message as a fact when it contains personal info or preferences."""
        if _looks_like_fact(user_message):
            self.store_fact(user_message.strip())

    def store_remember(self, text: str) -> None:
        """Persist a 'remember this' memory to memory_data/remembered.json.

        Survives restarts (it's a plain JSON file) and is loaded both into
        the prompt and into the UI's 3D bubble graph.
        """
        path = _repo_root() / "memory_data" / "remembered.json"
        items = self.load_remembered()
        # Dedupe by exact text.
        for it in items:
            if it.get("text") == text:
                return
        items.append({
            "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        try:
            path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[WARN] Failed to write remembered.json: {e}")

    def load_remembered(self) -> list[dict]:
        """Load all persisted 'remember' memories."""
        path = _repo_root() / "memory_data" / "remembered.json"
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def load_legacy_facts(self) -> list[str]:
        """Load facts from legacy facts.json and notes.md into the prompt."""
        facts_path = _repo_root() / "memory_data" / "facts.json"
        notes_path = _repo_root() / "memory_data" / "notes.md"
        lines: list[str] = []
        if facts_path.exists():
            try:
                facts = json.loads(facts_path.read_text(encoding="utf-8"))
                for f in facts:
                    lines.append(f"- {f.get('text', '')}")
            except Exception:
                pass
        if notes_path.exists():
            try:
                for line in notes_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("- "):
                        lines.append(line)
            except Exception:
                pass
        # Include persisted "remember" memories too.
        for it in self.load_remembered():
            lines.append(f"- {it.get('text', '')}")
        return lines

    def load_routines(self) -> list[dict]:
        """Load learned routines from memory_data/routines/*.json."""
        routines_dir = _repo_root() / "memory_data" / "routines"
        routines: list[dict] = []
        if routines_dir.exists():
            for rf in sorted(routines_dir.glob("*.json")):
                try:
                    d = json.loads(rf.read_text(encoding="utf-8"))
                    if d.get("name"):
                        routines.append(d)
                except Exception:
                    pass
        return routines
