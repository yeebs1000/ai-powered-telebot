"""Pick one Telegram reaction emoji for a message, or nothing.

Still free — no model call per message. What changed is precision.

The previous version tested `trigger in text.lower()`, so any trigger buried
inside a longer word fired: "trip", "gripping", "description" and "scripted"
all matched "rip" and got 😢; "gloves" and "clover" matched "love"; "1000"
matched "100". On this group's own history that was 1 wrong out of 4.

Three rules now:
  * triggers match on word boundaries, so a substring cannot fire one;
  * elongation collapses first, because people type "sooo" and "hahahaha";
  * every category is scored and the strongest wins, rather than whichever
    rule happened to sit highest in the list.

Sparsity is still the point. Most messages get nothing, questions are left
alone (a question wants an answer, not applause), and a per-chat cooldown
stops a lively exchange turning into a wall of bot reactions.

Only Telegram's free reaction set is allowed; any other emoji makes
set_reaction fail with 400.
"""
from __future__ import annotations

import re
import time

# Collapse runs of 3+ identical letters: "sooo" -> "soo", "hahahaha" -> "haha".
_ELONGATED = re.compile(r"(.)\1{2,}")
_ANY_REPEAT = re.compile(r"(.)\1+")
_PUNCT = re.compile(r"[^\w\s'\U0001F300-\U0001FAFF☀-➿]+")


def normalize(text: str) -> str:
    """Collapse runs of 3+ to 2, so "sooo" -> "soo" but "cool" survives."""
    low = (text or "").lower()
    low = _ELONGATED.sub(r"\1\1", low)
    return _PUNCT.sub(" ", low)


def _flatten(text: str) -> str:
    """Collapse every run to a single letter: "thankssss" -> "thanks".

    Matching tries this as well as normalize(), because a doubled tail still
    breaks a word-boundary match. It would mangle a trigger that legitimately
    contains a double letter, so it is an extra attempt, never a replacement.
    """
    return _PUNCT.sub(" ", _ANY_REPEAT.sub(r"\1", (text or "").lower()))


# (emoji, phrases). Phrases match whole words; multi-word phrases match in order.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("🤣", ("hilarious", "joke", "joking", "cracking me up", "so funny",
            "dead", "crying")),
    ("🎉", ("congrats", "congratulations", "we won", "winner", "got the job",
            "promoted", "passed", "nailed it", "happy birthday", "well done")),
    ("🔥", ("goated", "lets go", "let's go", "insane", "banger", "cracked",
            "beast", "sick", "legend", "clean", "class")),
    ("❤", ("love", "loved", "gorgeous", "beautiful", "adorable", "cute", "sweet")),
    ("😢", ("rip", "so sad", "heartbroken", "that sucks", "gutted",
            "condolences", "devastated", "unlucky", "gutting")),
    ("🤯", ("wow", "no way", "unbelievable", "shocked", "cant believe",
            "can't believe", "mind blown", "insane bro", "what the")),
    ("🙏", ("thank you", "thanks", "thx", "appreciate it", "appreciated",
            "grateful", "much appreciated")),
    ("💯", ("facts", "exactly", "true that", "well said", "spot on",
            "couldn't agree more", "couldnt agree more", "100 percent")),
    ("👀", ("interesting", "tell me more", "go on", "spill", "wait what")),
    ("🤝", ("deal", "agreed", "sounds good", "im in", "i'm in", "count me in")),
]

# Emoji the sender used that we can mirror straight back.
_MIRRORABLE = {"😂": "🤣", "🤣": "🤣", "🔥": "🔥", "❤": "❤", "😍": "❤", "🥰": "❤",
               "😢": "😢", "😭": "😢", "🎉": "🎉", "💯": "💯", "🙏": "🙏",
               "👍": "👍", "🤯": "🤯", "👏": "👏", "😱": "😱"}

# Laughter does not obey word boundaries. Real messages here include
# "HAHAHAHAH" and "GAHAHAHAH", where the run is embedded in a longer token, and
# "lmaooooo", where elongation leaves a doubled vowel. Matched separately.
_LAUGHTER = [
    re.compile(r"(?:h[ae]){2,}"),        # haha, hehe, hahaha, gahahah
    re.compile(r"(?<!\w)lo+l+(?:z|s)?(?!\w)"),
    re.compile(r"(?<!\w)l+m+f?a+o+(?!\w)"),
    re.compile(r"(?<!\w)rofl(?!\w)"),
]

_COMPILED = [
    (emoji, [re.compile(r"(?<!\w)" + re.escape(normalize(p)).replace(r"\ ", r"\s+") + r"(?!\w)")
             for p in phrases])
    for emoji, phrases in _RULES
]


def pick_reaction(text: str) -> str | None:
    """One emoji, or None to stay silent."""
    if not text or not text.strip():
        return None

    stripped = text.strip()

    # An emoji-only message is a reaction itself — mirror it.
    if not re.search(r"[a-zA-Z0-9]", stripped):
        for ch in stripped:
            if ch in _MIRRORABLE:
                return _MIRRORABLE[ch]
        return None

    # A question wants an answer, not applause.
    if stripped.endswith("?"):
        return None

    low = normalize(stripped)
    flat = _flatten(stripped)
    scores: dict[str, int] = {}
    for emoji, patterns in _COMPILED:
        hits = sum(1 for pat in patterns if (pat.search(low) or pat.search(flat)))
        if hits:
            scores[emoji] = scores.get(emoji, 0) + hits
    laughs = sum(1 for pat in _LAUGHTER if (pat.search(low) or pat.search(flat)))
    if laughs:
        scores["🤣"] = scores.get("🤣", 0) + laughs

    if not scores:
        return None
    return max(scores.items(), key=lambda kv: kv[1])[0]


class ReactionLimiter:
    """Per-chat cooldown. Sparsity is a feature: a burst of bot reactions in a
    fast-moving group reads as noise, not presence."""

    def __init__(self, cooldown_seconds: float = 45.0):
        self.cooldown = cooldown_seconds
        self._last: dict[int, float] = {}

    def allow(self, chat_id: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        last = self._last.get(chat_id)
        if last is not None and now - last < self.cooldown:
            return False
        self._last[chat_id] = now
        return True
