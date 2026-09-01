"""Local SQLite store — replaces Supabase for chat logs, embeddings, and polls.

Everything the bot remembers lives in one file on this box. No network hop, no
cloud account, no service key. The three tables mirror the Postgres schema
this replaced (see CHANGELOG), so the feature behaviour is unchanged:

    group_chat_logs   raw group history          -> /summarize, roast
    group_embeddings  768-dim vectors + message  -> semantic memory search
    active_polls      live inline-keyboard polls -> 5-minute vote windows

Semantic search is brute-force cosine over numpy, which replaces the pgvector
`match_chat_embeddings` RPC the hosted schema provided. At group-chat volumes (thousands of messages) that
is a sub-millisecond dot product and needs no extension, no index, and no
tuning. If a chat ever grows past ~100k embedded messages, revisit it — that is
the point where an ANN index starts to earn its complexity, not before.

Callers run these from asyncio.to_thread, so every method takes a lock and the
connection is opened with check_same_thread=False. WAL keeps the projector's
reads from blocking the bot's writes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS group_chat_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    sender     TEXT    NOT NULL,
    message    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_chat_time ON group_chat_logs (chat_id, created_at);

CREATE TABLE IF NOT EXISTS group_embeddings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    sender     TEXT    NOT NULL,
    message    TEXT    NOT NULL,
    -- float32 little-endian, dim inferred from length. Stored as a BLOB rather
    -- than JSON so a few thousand vectors load as one numpy buffer, not a parse.
    embedding  BLOB    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emb_chat ON group_embeddings (chat_id);

CREATE TABLE IF NOT EXISTS active_polls (
    poll_id    TEXT PRIMARY KEY,
    chat_id    INTEGER NOT NULL,
    question   TEXT    NOT NULL,
    options    TEXT    NOT NULL,   -- JSON: {option: vote_count}
    votes      TEXT    NOT NULL DEFAULT '{}',  -- JSON: {user_id: option}
    expires_at TEXT    NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        # The log holds other people's messages. sqlite creates the file with
        # the process umask, which is world-readable by default.
        try:
            self.path.chmod(0o600)
        except OSError:  # pragma: no cover -- e.g. a read-only bind in a test
            pass
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ── CHAT LOGS ────────────────────────────────────────────────────────────

    def log_message(self, chat_id: int, sender: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO group_chat_logs (chat_id, sender, message, created_at)"
                " VALUES (?, ?, ?, ?)",
                (chat_id, sender, message, _utcnow()),
            )
            self._conn.commit()

    def recent_messages(self, chat_id: int, limit: int = 500) -> list[dict]:
        """Newest `limit` messages, returned oldest-first so the model reads
        them in the order they were said."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT sender, message FROM group_chat_logs WHERE chat_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def messages_by_sender(self, chat_id: int, sender: str, limit: int = 200) -> list[dict]:
        """Case-insensitive substring match on sender — the ilike('%name%') the
        roast feature used."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT sender, message FROM group_chat_logs"
                " WHERE chat_id = ? AND sender LIKE ? COLLATE NOCASE LIMIT ?",
                (chat_id, f"%{sender}%", limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def messages_in_range(self, start: str, end: str) -> list[dict]:
        """Everything logged in [start, end) across all chats — the projector's
        only read. Ordered so a daily note renders chronologically."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT chat_id, sender, message, created_at FROM group_chat_logs"
                " WHERE created_at >= ? AND created_at < ?"
                " ORDER BY chat_id, created_at, id",
                (start, end),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── EMBEDDINGS ───────────────────────────────────────────────────────────

    def add_embedding(self, chat_id: int, sender: str, message: str, vec) -> None:
        blob = np.asarray(vec, dtype=np.float32).tobytes()
        with self._lock:
            self._conn.execute(
                "INSERT INTO group_embeddings (chat_id, sender, message, embedding, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (chat_id, sender, message, blob, _utcnow()),
            )
            self._conn.commit()

    def match_embeddings(
        self, chat_id: int, vec, threshold: float = 0.3, count: int = 5
    ) -> list[dict]:
        """Cosine similarity, highest first, above `threshold`.

        Replaces the pgvector RPC. Returns the same shape the caller expected:
        sender, message, similarity.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT sender, message, embedding FROM group_embeddings WHERE chat_id = ?",
                (chat_id,),
            ).fetchall()
        if not rows:
            return []

        query = np.asarray(vec, dtype=np.float32)
        qnorm = np.linalg.norm(query)
        if qnorm == 0:
            return []

        mat, keep = [], []
        for r in rows:
            v = np.frombuffer(r["embedding"], dtype=np.float32)
            # A dimension change (switching embedding models) would otherwise
            # raise deep inside the dot product. Skip mismatches instead: the
            # old vectors are simply unsearchable until re-embedded.
            if v.shape[0] != query.shape[0]:
                continue
            mat.append(v)
            keep.append(r)
        if not mat:
            return []

        mat = np.vstack(mat)
        norms = np.linalg.norm(mat, axis=1)
        norms[norms == 0] = 1e-12
        sims = (mat @ query) / (norms * qnorm)

        order = np.argsort(-sims)[:count]
        return [
            {"sender": keep[i]["sender"], "message": keep[i]["message"],
             "similarity": float(sims[i])}
            for i in order
            if sims[i] > threshold
        ]

    # ── POLLS ────────────────────────────────────────────────────────────────

    def create_poll(self, poll_id: str, chat_id: int, question: str,
                    options: dict, expires_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO active_polls (poll_id, chat_id, question, options, votes, expires_at)"
                " VALUES (?, ?, ?, ?, '{}', ?)",
                (poll_id, chat_id, question, json.dumps(options), expires_at),
            )
            self._conn.commit()

    def get_poll(self, poll_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM active_polls WHERE poll_id = ?", (poll_id,)
            ).fetchone()
        if not row:
            return None
        poll = dict(row)
        poll["options"] = json.loads(poll["options"])
        poll["votes"] = json.loads(poll["votes"])
        return poll

    def update_poll(self, poll_id: str, options: dict, votes: dict) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE active_polls SET options = ?, votes = ? WHERE poll_id = ?",
                (json.dumps(options), json.dumps(votes), poll_id),
            )
            self._conn.commit()

    def delete_poll(self, poll_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM active_polls WHERE poll_id = ?", (poll_id,))
            self._conn.commit()
