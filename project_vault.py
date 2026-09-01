#!/usr/bin/env python3
"""Project Telegram chat facts into YeebsVault as reviewable daily notes.

WHAT THIS IS NOT: it is not the store. The database of record is the SQLite
file at TELEBOT_DB; this renders a read-only view of it. Deleting everything
under OWNED_DIR and re-running reproduces it exactly.

WHAT IT WRITES: only paths under `13 AI/Telegram/`, reached through a spaceless
bind symlink (systemd BindPaths does not honour a space in a path). telebot has
an ACL on that folder alone and cannot traverse the vault root, so this can
never read or write `08 Persona - Owner Only`.

CLASSIFICATION: every note is `local_only`. These are other people's messages;
the Vault Gateway must never surface them to a cloud audience.

The deterministic sections are counts and terms computed from the rows. The
recap section is model-written and labelled as such -- if the model is
unreachable the section is omitted, never faked.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytz

sys.path.insert(0, "/opt/telebot")
from store import Store  # noqa: E402

TZ = pytz.timezone("Asia/Singapore")
AI_ROOT = Path(os.environ.get("YEEBS_AI_ROOT", "/var/lib/syncthing/YeebsVault/13 AI"))
OWNED_DIR = AI_ROOT / "Telegram" / "Daily"
INDEX = AI_ROOT / "Telegram" / "Telegram Daily Index.md"
DB = os.environ.get("TELEBOT_DB", "/var/lib/telebot/telebot.db")

HUMAN_START = "<!-- yeebs:human:start -->"
HUMAN_END = "<!-- yeebs:human:end -->"
GEN_START = "<!-- yeebs:generated:start -->"
GEN_END = "<!-- yeebs:generated:end -->"

# Deliberately small and boring: enough to stop "the/and/i" dominating every
# note, not a real NLP stack. A term list is a hint for the reader, not a claim.
STOPWORDS = set("""
a about all also am an and any are as at be because been but by can cant cause come could did
do does doesnt dont for from get go going good got had has have he her here hes him his how i
id if ill im in into is isnt it its ive just know like ll me more most much my no not now of oh
ok on one only or other our out over really right said say see she should so some still such
than that thats the their them then there these they thing think this those though to too two up
us very want was way we well went were what when where which who why will with would yeah yes
yet you your youre u ur lol lmao haha ok okay dont im
""".split())

WORD = re.compile(r"[a-z][a-z'-]{2,}")


def _preserved_human_block(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if HUMAN_START in text and HUMAN_END in text:
        # Strip the surrounding newlines the markers are written with. Without
        # this the block gains a blank line on every run and grows forever.
        return text.split(HUMAN_START, 1)[1].split(HUMAN_END, 1)[0].strip("\n")
    return ""


def _day_bounds(day: datetime) -> tuple[str, str]:
    """[start, end) as UTC ISO strings for a Singapore calendar day, matching
    how created_at is written."""
    start_local = TZ.localize(datetime(day.year, day.month, day.day))
    end_local = start_local + timedelta(days=1)
    return (start_local.astimezone(pytz.utc).isoformat(),
            end_local.astimezone(pytz.utc).isoformat())


def _recap(rows: list[dict]) -> str | None:
    """Ask the local model for a short recap. Returns None on any failure --
    an absent recap is correct; an invented one is not."""
    if not rows or os.environ.get("TELEBOT_VAULT_RECAP", "1") != "1":
        return None
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", "ollama"),
            base_url=os.environ["OPENAI_BASE_URL"],
        )
        transcript = "\n".join(f"{r['sender']}: {r['message']}" for r in rows[:400])
        resp = client.chat.completions.create(
            model=os.environ.get("AI_MODEL", "qwen3.5-agent:32k"),
            messages=[{
                "role": "user",
                "content": (
                    "Summarise this group chat day in 3-5 short bullet points. "
                    "Only state what is actually in the log. No preamble.\n\n"
                    f"{transcript}"
                ),
            }],
            timeout=180,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception as e:  # noqa: BLE001 -- any failure means "skip the section"
        print(f"recap skipped: {e}", file=sys.stderr)
        return None


def render_day(store: Store, day: datetime) -> Path | None:
    start, end = _day_bounds(day)
    rows = store.messages_in_range(start, end)
    if not rows:
        return None

    datestr = day.strftime("%Y-%m-%d")
    by_chat: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_chat[r["chat_id"]].append(r)

    senders = Counter(r["sender"] for r in rows)
    hours = Counter(
        datetime.fromisoformat(r["created_at"]).astimezone(TZ).hour for r in rows
    )
    terms = Counter(
        w for r in rows for w in WORD.findall(r["message"].lower()) if w not in STOPWORDS
    )

    out = [
        "---",
        "type: telegram-daily",
        "database: ai",
        f"date: {datestr}",
        "classification: local_only",
        f"generated: {datetime.now(TZ).isoformat(timespec='seconds')}",
        "---",
        "",
        f"# Telegram — {day.strftime('%Y-%m-%d %A')}",
        "",
        HUMAN_START,
        _preserved_human_block(OWNED_DIR / f"{datestr}.md"),
        HUMAN_END,
        "",
        GEN_START,
        "",
        "## Activity",
        "",
        f"- **{len(rows)}** messages across **{len(by_chat)}** chat(s)",
        f"- **{len(senders)}** participants",
    ]
    if hours:
        busiest = hours.most_common(1)[0]
        out.append(f"- Busiest hour: **{busiest[0]:02d}:00** ({busiest[1]} messages)")
    out += ["", "## Who talked", "", "| Sender | Messages |", "|---|---:|"]
    out += [f"| {s} | {c} |" for s, c in senders.most_common()]

    common = [f"`{t}`" for t, c in terms.most_common(12) if c > 1]
    if common:
        out += ["", "## Recurring terms", "", " · ".join(common)]

    recap = _recap(rows)
    if recap:
        out += ["", "## Recap", "",
                f"> Written by `{os.environ.get('AI_MODEL', 'local model')}` "
                "from the log below. Treat as a reading aid, not a record.", ""]
        out += [recap]

    out += ["", "## Chats", ""]
    for chat_id, msgs in sorted(by_chat.items()):
        out.append(f"- Chat `{chat_id}` — {len(msgs)} messages, "
                   f"{len({m['sender'] for m in msgs})} participants")

    out += ["", GEN_END, ""]

    OWNED_DIR.mkdir(parents=True, exist_ok=True)
    path = OWNED_DIR / f"{datestr}.md"
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def render_index() -> None:
    notes = sorted(OWNED_DIR.glob("20*.md"), reverse=True) if OWNED_DIR.exists() else []
    body = [
        "---",
        "type: index",
        "database: ai",
        "classification: local_only",
        f"generated: {datetime.now(TZ).isoformat(timespec='seconds')}",
        "---",
        "",
        "# Telegram Daily Index",
        "",
        f"{len(notes)} day(s) projected from the telebot store. "
        "These notes are generated — edit only inside the human block.",
        "",
    ]
    body += [f"- [[{p.stem}]]" for p in notes]
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text("\n".join(body) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday, SGT)")
    ap.add_argument("--days", type=int, default=1, help="how many days back to render")
    args = ap.parse_args()

    if args.date:
        anchor = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        anchor = datetime.now(TZ).replace(tzinfo=None) - timedelta(days=1)

    store = Store(DB)
    written = 0
    for i in range(args.days):
        day = anchor - timedelta(days=i)
        path = render_day(store, day)
        if path:
            written += 1
            print(f"wrote {path}")
        else:
            print(f"no messages for {day:%Y-%m-%d} — skipped")
    render_index()
    print(f"done: {written} note(s), index refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
