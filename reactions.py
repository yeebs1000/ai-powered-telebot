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
    fast-moving group reads as noise, not presence.

    Ten minutes, not the original forty-five seconds. A lively exchange
    produces a message every few seconds; at 45s the bot punctuated the same
    conversation repeatedly, which is what "why tf the ai laugh" in the logs
    was about."""

    def __init__(self, cooldown_seconds: float = 600.0):
        self.cooldown = cooldown_seconds
        self._last: dict[int, float] = {}

    def allow(self, chat_id: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        last = self._last.get(chat_id)
        if last is not None and now - last < self.cooldown:
            return False
        self._last[chat_id] = now
        return True


# ═════════════════════════════════════════════════════════════════════════════
# SEMANTIC REACTIONS
# ═════════════════════════════════════════════════════════════════════════════
# Keywords only ever match what someone thought to list. "my grandad passed
# away last night" contains no trigger word and got nothing; "gutted we lost"
# and "devastated" were separate entries that both had to be remembered.
#
# This compares the message against exemplar phrases per emoji in embedding
# space, so meaning matches rather than spelling. The cost is one embedding --
# which the bot ALREADY computes for every logged group message, so on the
# common path this is free; the vector is computed once and used twice.
#
# Keywords stay as the fallback for when embeddings are unavailable (no embed
# model configured, or the call failed). They are cheap and they are not wrong,
# they are just narrow.

import json
import logging
import math
import os

_log = logging.getLogger(__name__)

# Several phrasings each, so the centroid describes a feeling rather than a
# sentence. These are matched by meaning, so near-synonyms are not needed.
_EXEMPLARS: dict[str, tuple[str, ...]] = {
    "🤣": ("that is hilarious", "I can't stop laughing", "this is so funny",
           "absolutely cracking up at this", "what a ridiculous joke"),
    "🔥": ("that is seriously impressive", "this looks incredible",
           "absolutely brilliant performance", "that was a great result",
           "this is really well done"),
    "❤": ("I love this so much", "that is adorable",
          "this is beautiful", "so sweet of you", "I really care about this"),
    "😢": ("my grandfather passed away", "I'm heartbroken about it",
           "that is really sad news", "we lost and I'm gutted",
           "sorry for your loss", "I'm feeling really down today"),
    "🤯": ("I cannot believe that happened", "that is completely unexpected",
           "this blew my mind", "no way that is real", "I am genuinely shocked"),
    "🙏": ("thank you so much for helping", "I really appreciate it",
           "grateful for your support", "thanks for sorting that out"),
    "💯": ("that is exactly right", "completely agree with this",
           "you said it perfectly", "this is spot on"),
}

# Removed 2026-09-03: 🤝, 👀, 🎉 and 😨.
#
# 🎉 fired on "Better than I thought", "my bro is back up" and "my bot is back
# alive rn" — none of them celebrations. In embedding space it was matching
# "something good happened", which is most of a cheerful conversation.
# Congratulations are stated explicitly when they are meant ("congrats", "we
# won", "got the job"), so the keyword table catches them without the vibe
# matching. What the semantic layer is *for* is the case keywords cannot
# reach: "my grandad passed away last night", which no trigger word covers.
#
# 😨 went for a different reason: with this embedding model it put "sounds good
# to me" at 0.643, nearer than anything else, and rephrasing the exemplars away
# from "that sounds..." did not move it. A category that cannot be separated
# from a common phrase is not worth a wrong 😨 on someone's news.
# Measured on 141 real messages, 🤝 alone was 14 of 20 semantic reactions and
# almost none were agreement: "So u enjoy anot", "Yeah its a fan ah", "My
# shopee one works the same", "i think cuz i j deployed it last night". They
# describe a conversational move rather than a feeling, and in embedding space
# nearly any relaxed message sits near "sounds good to me". A category that
# matches ordinary talk cannot be rescued with a threshold.


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


class SemanticReactor:
    """Picks a reaction by meaning. Needs an async embed(text) -> list[float].

    Exemplar vectors are computed once and cached on disk: they only change
    when the exemplar list or the embedding model changes, and recomputing ~45
    embeddings on every restart would make startup needlessly slow.
    """

    # Below this there is not enough text for an embedding to mean anything.
    # "ok" landed on 🤝 purely because short strings sit near everything.
    MIN_CHARS = 14

    def __init__(self, embed, cache_path: str | None = None,
                 threshold: float = 0.58, margin: float = 0.02):
        self._embed = embed
        self._cache_path = cache_path
        self.threshold = threshold
        self.margin = margin
        self._centroids: dict[str, list[float]] = {}
        self._ready = False

    def _cache_key(self) -> str:
        return json.dumps({e: list(v) for e, v in sorted(_EXEMPLARS.items())},
                          sort_keys=True)

    async def prepare(self) -> bool:
        if self._ready:
            return True
        if self._cache_path and os.path.exists(self._cache_path):
            try:
                with open(self._cache_path) as fh:
                    blob = json.load(fh)
                if blob.get("key") == self._cache_key():
                    self._centroids = {k: v for k, v in blob["centroids"].items()}
                    self._ready = True
                    _log.info(f"semantic reactions: {len(self._centroids)} centroids from cache")
                    return True
            except Exception as e:
                _log.warning(f"semantic reactions: cache unusable ({e}); recomputing")

        try:
            for emoji, phrases in _EXEMPLARS.items():
                vecs = [await self._embed(p) for p in phrases]
                dim = len(vecs[0])
                self._centroids[emoji] = [
                    sum(v[i] for v in vecs) / len(vecs) for i in range(dim)
                ]
        except Exception as e:
            _log.error(f"semantic reactions unavailable, keywords only: {e}")
            self._centroids = {}
            return False

        self._ready = True
        if self._cache_path:
            try:
                tmp = self._cache_path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump({"key": self._cache_key(),
                               "centroids": self._centroids}, fh)
                os.replace(tmp, self._cache_path)
            except Exception as e:
                _log.warning(f"semantic reactions: could not cache ({e})")
        _log.info(f"semantic reactions: {len(self._centroids)} centroids computed")
        return True

    def pick_from_vector(self, vec, text: str | None = None) -> str | None:
        """Nearest emoji above threshold, or None. Two categories within
        `margin` of each other means the feeling is unclear -- stay silent
        rather than pick the marginally closer one."""
        if not self._centroids or not vec:
            return None
        # The keyword path has always stayed quiet on questions; the vector
        # path did not, and answered "Does it work though?" with 🤝.
        if text is not None and text.strip().endswith("?"):
            return None
        scored = sorted(((_cosine(vec, c), e) for e, c in self._centroids.items()),
                        reverse=True)
        if not scored or scored[0][0] < self.threshold:
            return None
        if len(scored) > 1 and scored[0][0] - scored[1][0] < self.margin:
            return None
        return scored[0][1]

    def worth_embedding(self, text: str) -> bool:
        return bool(text) and len(text.strip()) >= self.MIN_CHARS

    async def pick(self, text: str) -> str | None:
        if not self.worth_embedding(text):
            return None
        if not self._ready and not await self.prepare():
            return None
        try:
            return self.pick_from_vector(await self._embed(text), text)
        except Exception as e:
            _log.error(f"semantic reaction embed failed: {e}")
            return None
