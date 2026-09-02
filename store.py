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

import difflib
import json
import re
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

-- Who is in a chat. Telegram user_id is the only stable identity: display
-- names change, and some are unmatchable by name at all (emoji handles), which
-- is exactly why aliases exist and why messages carry a user_id.
CREATE TABLE IF NOT EXISTS members (
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    first_name TEXT,
    username   TEXT,
    aliases    TEXT NOT NULL DEFAULT '[]',  -- JSON list, names taught by the group
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

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
        self._migrate()

    def _migrate(self) -> None:
        """Add user_id to the message tables. Rows written before identity
        tracking keep NULL -- they are still searchable by name, just not
        attributable to an account."""
        for table in ("group_chat_logs", "group_embeddings"):
            cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if "user_id" not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
        self._conn.commit()

    # ── MEMBERS / IDENTITY ───────────────────────────────────────────────────

    def upsert_member(self, chat_id: int, user_id: int,
                      first_name: str | None, username: str | None) -> None:
        """Called on every message. Refreshes the display name so a rename is
        picked up, without disturbing aliases the group has taught."""
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                "INSERT INTO members (chat_id, user_id, first_name, username,"
                " first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(chat_id, user_id) DO UPDATE SET"
                " first_name = excluded.first_name,"
                " username = excluded.username,"
                " last_seen = excluded.last_seen",
                (chat_id, user_id, first_name, username, now, now),
            )
            self._conn.commit()

    def list_members(self, chat_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM members WHERE chat_id = ? ORDER BY last_seen DESC",
                (chat_id,),
            ).fetchall()
        out = []
        for r in rows:
            m = dict(r)
            m["aliases"] = json.loads(m["aliases"])
            out.append(m)
        return out

    def add_alias(self, chat_id: int, user_id: int, alias: str) -> bool:
        """Teach the group's name for someone. Returns False if already known."""
        alias = alias.strip()
        if not alias:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT aliases FROM members WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
            if row is None:
                return False
            aliases = json.loads(row["aliases"])
            if any(a.lower() == alias.lower() for a in aliases):
                return False
            aliases.append(alias)
            self._conn.execute(
                "UPDATE members SET aliases = ? WHERE chat_id = ? AND user_id = ?",
                (json.dumps(aliases), chat_id, user_id),
            )
            self._conn.commit()
        return True

    def resolve_member(self, chat_id: int, name: str, cutoff: float = 0.62) -> dict | None:
        """Best member for a spoken name, or None.

        Tried in order, most confident first: exact match, taught alias,
        substring, then fuzzy. Names in this group are unique, so a single
        best match is the right answer -- but an ambiguous fuzzy result
        returns None rather than guessing between two people.

        The cutoff is deliberately loose (a misspelling like "shaun" for
        "sean" only scores 0.67). What keeps that safe is the ambiguity
        check below, not the threshold: a loose cutoff with two plausible
        candidates refuses, where a tight one would simply miss.
        """
        name = (name or "").strip().lstrip("@")
        if not name:
            return None
        members = self.list_members(chat_id)
        if not members:
            return None

        def names_of(m):
            return [n for n in ([m["first_name"], m["username"]] + m["aliases"]) if n]

        low = name.lower()
        for m in members:                                    # exact
            if any(n.lower() == low for n in names_of(m)):
                return {**m, "match": "exact"}
        for m in members:                                    # substring either way
            for n in names_of(m):
                nl = n.lower()
                if low in nl or nl in low:
                    return {**m, "match": "substring"}

        # Fuzzy, ignoring spacing/punctuation so "yuanbing" reaches "Yuan Bing".
        norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
        target = norm(name)
        scored = []
        for m in members:
            best = max((difflib.SequenceMatcher(None, target, norm(n)).ratio()
                        for n in names_of(m) if norm(n)), default=0.0)
            scored.append((best, m))
        scored.sort(key=lambda x: -x[0])
        if not scored or scored[0][0] < cutoff:
            return None
        # Two people equally close is not a match -- ask, do not guess.
        if len(scored) > 1 and scored[1][0] >= scored[0][0] - 0.05:
            return None
        return {**scored[0][1], "match": "fuzzy", "score": round(scored[0][0], 3)}

    def messages_by_member(self, chat_id: int, user_id: int,
                           first_name: str | None = None,
                           limit: int = 200) -> list[dict]:
        """A member's messages by stable id, falling back to their display name
        so history logged before identity tracking still counts."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT sender, message FROM group_chat_logs"
                " WHERE chat_id = ? AND (user_id = ? OR (user_id IS NULL AND sender = ?))"
                " ORDER BY created_at DESC LIMIT ?",
                (chat_id, user_id, first_name, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── CHAT LOGS ────────────────────────────────────────────────────────────

    def log_message(self, chat_id: int, sender: str, message: str,
                    user_id: int | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO group_chat_logs (chat_id, sender, message, created_at, user_id)"
                " VALUES (?, ?, ?, ?, ?)",
                (chat_id, sender, message, _utcnow(), user_id),
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

    def messages_since_user_last(self, chat_id: int, user_id: int | None,
                                 sender: str | None, limit: int = 500) -> tuple[list[dict], str | None]:
        """Everything said since this person last spoke here.

        Returns (messages, since) where `since` is the timestamp of their last
        message, or None when they have never posted in this chat -- the caller
        distinguishes "nothing happened" from "you have no anchor here", which
        are different answers.

        Directed messages (the ones that mention the bot) are never logged, so
        the anchor is genuinely the last thing they said TO THE GROUP, not the
        request that triggered this.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT created_at FROM group_chat_logs"
                " WHERE chat_id = ? AND (user_id = ? OR (user_id IS NULL AND sender = ?))"
                " ORDER BY created_at DESC, id DESC LIMIT 1",
                (chat_id, user_id, sender),
            ).fetchone()
            if row is None:
                return [], None
            since = row["created_at"]
            rows = self._conn.execute(
                "SELECT sender, message, created_at FROM group_chat_logs"
                " WHERE chat_id = ? AND created_at > ?"
                " ORDER BY created_at, id LIMIT ?",
                (chat_id, since, limit),
            ).fetchall()
        return [dict(r) for r in rows], since

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

    def add_embedding(self, chat_id: int, sender: str, message: str, vec,
                      user_id: int | None = None) -> None:
        blob = np.asarray(vec, dtype=np.float32).tobytes()
        with self._lock:
            self._conn.execute(
                "INSERT INTO group_embeddings (chat_id, sender, message, embedding, created_at, user_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, sender, message, blob, _utcnow(), user_id),
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
