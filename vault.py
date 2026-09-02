"""Read-only reference notes from one vault folder.

SCOPE IS THE SECURITY MODEL. The bot sees a single folder, mounted read-only
by the systemd unit (BindReadOnlyPaths). It cannot reach the rest of the vault
-- not the journal, not trading, not `08 Persona - Owner Only` -- so a note
misclassified elsewhere can never reach a group chat through this path.

Anything in the folder may end up quoted to everyone in the group. A note
carrying `classification: deny` is skipped anyway, as a second line of defence.

Retrieval is keyword overlap, not embeddings: this runs on the reply path where
the latency budget is a couple of seconds, the corpus is small and hand-curated,
and a deterministic match is easier to reason about when you are deciding what
the bot is allowed to say.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

WORD = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")

# Query words too common to indicate a topic.
STOPWORDS = set("""
about all and any are ask because been but can cant did does dont for from get
got had has have her him his how its just know like more most much not now our
out say see she some than that the their them then there these they this those
too was way were what when where which who why will with would you your yours
""".split())


def _tokens(text: str) -> set[str]:
    return {w for w in WORD.findall((text or "").lower()) if w not in STOPWORDS}


class VaultReference:
    def __init__(self, root: str | os.PathLike | None):
        self.root = Path(root) if root else None
        self._cache: list[dict] = []
        self._stamp: tuple | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.root and self.root.is_dir())

    def _stamp_now(self) -> tuple:
        """Cheap change detector: every note's path, size and mtime."""
        out = []
        for p in sorted(self.root.rglob("*.md")):
            try:
                st = p.stat()
            except OSError:
                continue
            out.append((str(p), st.st_size, int(st.st_mtime)))
        return tuple(out)

    def _load(self) -> None:
        if not self.enabled:
            self._cache = []
            return
        stamp = self._stamp_now()
        if stamp == self._stamp:
            return
        notes = []
        for path, _, _ in stamp:
            p = Path(path)
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                logger.warning(f"vault: cannot read {p.name}: {e}")
                continue
            body, classification = raw, None
            if raw.startswith("---"):
                parts = raw.split("---", 2)
                if len(parts) >= 3:
                    fm, body = parts[1], parts[2]
                    m = re.search(r"^\s*classification:\s*(\S+)", fm, re.M)
                    classification = m.group(1).strip() if m else None
            if (classification or "").lower() == "deny":
                logger.info(f"vault: skipping {p.name} (classification: deny)")
                continue
            title = p.stem
            notes.append({
                "title": title,
                "path": str(p),
                "body": body.strip(),
                "title_tokens": _tokens(title),
                "body_tokens": _tokens(body),
            })
        self._cache = notes
        self._stamp = stamp
        logger.info(f"vault: loaded {len(notes)} reference note(s)")

    def search(self, query: str, limit: int = 2, min_score: float = 0.12) -> list[dict]:
        self._load()
        q = _tokens(query)
        if not q or not self._cache:
            return []
        scored = []
        for n in self._cache:
            # A title hit is worth far more than a body hit: it means the note
            # is about the thing asked, not that it mentions it in passing.
            hits = len(q & n["title_tokens"]) * 3 + len(q & n["body_tokens"])
            if hits:
                scored.append((hits / (len(q) * 3), n))
        scored.sort(key=lambda x: -x[0])
        return [{"title": n["title"], "body": n["body"], "score": round(sc, 3)}
                for sc, n in scored[:limit] if sc >= min_score]

    def context_block(self, query: str, max_chars: int = 1200) -> str:
        """Reference text to prepend to a prompt, or "" when nothing matches."""
        hits = self.search(query)
        if not hits:
            return ""
        parts = []
        budget = max_chars
        for h in hits:
            excerpt = h["body"][:budget].strip()
            if not excerpt:
                break
            parts.append(f"## {h['title']}\n{excerpt}")
            budget -= len(excerpt)
            if budget <= 0:
                break
        if not parts:
            return ""
        return ("[Reference notes — background you may use. Do not read them "
                "aloud verbatim unless asked.]\n" + "\n\n".join(parts))
