"""Style profiles derived from what the group has actually said.

This is the "learn the members" layer, and it is deliberately NOT fine-tuning.
Fine-tuning bakes a snapshot into a checkpoint that goes stale the moment the
group says something new, needs far more data than a chat log provides, and
costs a rebuild to update. Aggregating the log costs milliseconds, improves
with every message, and can be inspected and corrected by a human reading it.

What it measures, per member and for the group as a whole:
  * distinctive vocabulary -- words they use more than the group baseline,
    which is what actually makes someone sound like themselves;
  * shared vocabulary -- what the whole group talks about;
  * habits: typical message length, emoji use, capitals, question rate.

The output is a short prompt block, not a rule. The model is told how people
talk so replies land in the group's register; nothing here decides what to say.
"""
from __future__ import annotations

import re
import time
from collections import Counter

WORD = re.compile(r"[a-z][a-z'\-]{1,}")
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")

# Function words carry no style signal; they would dominate every profile.
STOPWORDS = set("""
a about all also am an and any are as at be been but by can cant come could did
do does dont for from get go going good got had has have he her here hes him his
how i id if ill im in into is isnt it its ive just know like ll me more most much
my no not now of oh ok on one only or other our out over really right said say
see she should so some still such than that thats the their them then there these
they thing think this those though to too two up us very want was way we well
went were what when where which who why will with would yeah yes yet you your
youre u ur be will
""".split())

MIN_MESSAGES = 5          # below this a "profile" is noise dressed as insight
CACHE_TTL = 300.0         # seconds; the log changes slowly relative to replies


def _tokens(text: str) -> list[str]:
    return [w for w in WORD.findall((text or "").lower()) if w not in STOPWORDS]


class ProfileBuilder:
    def __init__(self, store, ttl: float = CACHE_TTL):
        self.store = store
        self.ttl = ttl
        self._cache: dict[int, tuple[float, dict]] = {}

    # ── computation ──────────────────────────────────────────────────────────

    def build(self, chat_id: int, limit: int = 2000) -> dict:
        rows = self.store.recent_messages(chat_id, limit=limit)
        by_sender: dict[str, list[str]] = {}
        for r in rows:
            by_sender.setdefault(r["sender"], []).append(r["message"])

        group_counts = Counter()
        for msgs in by_sender.values():
            for m in msgs:
                group_counts.update(set(_tokens(m)))
        total_msgs = sum(len(m) for m in by_sender.values())

        members = []
        for sender, msgs in by_sender.items():
            if len(msgs) < MIN_MESSAGES:
                continue
            own = Counter()
            for m in msgs:
                own.update(set(_tokens(m)))

            # Distinctive = used far more by this person than by the group at
            # large. A raw top-10 would just return the group's common words
            # for everyone, which reads as a profile but says nothing.
            distinctive = []
            for word, n in own.items():
                if n < 2:
                    continue
                own_rate = n / len(msgs)
                group_rate = group_counts[word] / max(total_msgs, 1)
                if own_rate > group_rate * 1.3:
                    distinctive.append((own_rate / max(group_rate, 1e-9), word, n))
            distinctive.sort(reverse=True)

            lengths = sorted(len(m) for m in msgs)
            members.append({
                "name": sender,
                "messages": len(msgs),
                "median_len": lengths[len(lengths) // 2],
                "words": [w for _, w, _ in distinctive[:8]],
                "emoji_rate": round(sum(1 for m in msgs if EMOJI.search(m)) / len(msgs), 2),
                "caps_rate": round(sum(1 for m in msgs
                                       if len(m) > 3 and m.isupper()) / len(msgs), 2),
                "question_rate": round(sum(1 for m in msgs if "?" in m) / len(msgs), 2),
                "top_emoji": [e for e, _ in Counter(
                    e for m in msgs for e in EMOJI.findall(m)).most_common(3)],
            })
        members.sort(key=lambda m: -m["messages"])

        return {
            "members": members,
            "group_words": [w for w, n in group_counts.most_common(15) if n > 1],
            "total_messages": total_msgs,
        }

    def get(self, chat_id: int) -> dict:
        hit = self._cache.get(chat_id)
        now = time.monotonic()
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        data = self.build(chat_id)
        self._cache[chat_id] = (now, data)
        return data

    def invalidate(self, chat_id: int | None = None) -> None:
        self._cache.pop(chat_id, None) if chat_id is not None else self._cache.clear()

    # ── prompt rendering ─────────────────────────────────────────────────────

    def _habits(self, m: dict) -> str:
        bits = []
        bits.append("very short messages" if m["median_len"] < 25
                    else "long messages" if m["median_len"] > 120 else None)
        if m["emoji_rate"] >= 0.4:
            bits.append(f"heavy emoji use ({' '.join(m['top_emoji'])})" if m["top_emoji"]
                        else "heavy emoji use")
        if m["caps_rate"] >= 0.15:
            bits.append("often types in caps")
        if m["question_rate"] >= 0.35:
            bits.append("asks a lot of questions")
        return ", ".join(b for b in bits if b)

    def group_block(self, chat_id: int, max_members: int = 6) -> str:
        """A compact description of how this group talks, or "" if too thin."""
        data = self.get(chat_id)
        if not data["members"]:
            return ""
        lines = ["[How this group talks — match their register, don't imitate "
                 "any one person or repeat this back.]"]
        if data["group_words"]:
            lines.append("Recurring topics: " + ", ".join(data["group_words"][:10]))
        for m in data["members"][:max_members]:
            desc = []
            if m["words"]:
                desc.append("says " + ", ".join(f"'{w}'" for w in m["words"][:5]))
            habits = self._habits(m)
            if habits:
                desc.append(habits)
            if desc:
                lines.append(f"- {m['name']}: " + "; ".join(desc))
        return "\n".join(lines) if len(lines) > 1 else ""

    def member_block(self, chat_id: int, name: str) -> str:
        """One member's style, for a personality question about them."""
        data = self.get(chat_id)
        low = (name or "").lower()
        for m in data["members"]:
            if (m["name"] or "").lower() == low:
                parts = [f"{m['name']} has {m['messages']} logged messages"]
                if m["words"]:
                    parts.append("characteristic words: " + ", ".join(m["words"]))
                habits = self._habits(m)
                if habits:
                    parts.append(f"habits: {habits}")
                return "[Style notes] " + "; ".join(parts)
        return ""
