#!/usr/bin/env python3
"""Check the configuration and say exactly what is wrong.

    python doctor.py            full check, including live calls
    python doctor.py --offline  configuration only, no network

Most "it doesn't work" reports are one missing variable, a model name the
provider does not have, or Telegram privacy mode left on. This finds those in
a few seconds and prints the fix, instead of leaving someone to read a
traceback or -- worse -- watch the bot sit there silently doing nothing.

Never prints a secret: keys are reported as present or missing, never echoed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OK, WARN, FAIL = "ok", "warn", "FAIL"
_MARK = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}

results: list[tuple[str, str, str]] = []


def record(status: str, what: str, detail: str = "") -> None:
    results.append((status, what, detail))
    line = f"[{_MARK[status]}] {what}"
    print(line if not detail else f"{line}\n           {detail}")


def _present(var: str) -> bool:
    return bool((os.getenv(var) or "").strip())


def check_python() -> None:
    v = sys.version_info
    if v >= (3, 11):
        record(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        record(FAIL, f"Python {v.major}.{v.minor} is too old",
               "Python 3.11 or newer is required.")


def check_dependencies() -> None:
    missing = []
    for mod, pkg in [("telegram", "python-telegram-bot"), ("httpx", "httpx"),
                     ("numpy", "numpy"), ("dotenv", "python-dotenv"),
                     ("pytz", "pytz")]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        record(FAIL, "Dependencies missing: " + ", ".join(missing),
               "pip install -r requirements.txt")
    else:
        record(OK, "Dependencies importable")


def check_telegram(offline: bool) -> str | None:
    if not _present("TELEGRAM_BOT_TOKEN"):
        record(FAIL, "TELEGRAM_BOT_TOKEN is not set",
               "Get one from @BotFather (/newbot), then put it in .env")
        return None
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    if ":" not in token or not token.split(":", 1)[0].isdigit():
        record(FAIL, "TELEGRAM_BOT_TOKEN does not look like a bot token",
               "Expected digits, a colon, then a long string.")
        return None
    record(OK, "TELEGRAM_BOT_TOKEN present", f"bot id {token.split(':', 1)[0]}")
    if offline:
        return None

    try:
        import httpx
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
        data = r.json()
    except Exception as e:
        record(WARN, "Could not reach Telegram", f"{type(e).__name__}: {e}")
        return None
    if not data.get("ok"):
        record(FAIL, "Telegram rejected the token",
               f"{data.get('error_code')}: {data.get('description')}")
        return None

    me = data["result"]
    record(OK, f"Token valid — @{me['username']}")
    if not me.get("can_read_all_group_messages"):
        record(WARN, "Privacy mode is ON — the bot cannot read group messages",
               "@BotFather → /setprivacy → your bot → Disable, then remove and "
               "re-add it to the group.")
    else:
        record(OK, "Privacy mode off (can read group messages)")
    return me["username"]


def check_provider(offline: bool):
    from providers import PRESETS, SUPPORTED, get_provider

    name = (os.getenv("AI_PROVIDER") or "").strip().lower()
    if not name:
        record(FAIL, "AI_PROVIDER is not set",
               f"Pick one of: {', '.join(SUPPORTED)}")
        return None
    if name not in SUPPORTED:
        record(FAIL, f"AI_PROVIDER='{name}' is not supported",
               f"Pick one of: {', '.join(SUPPORTED)}")
        return None

    try:
        provider = get_provider(name)
    except ValueError as e:
        record(FAIL, f"Provider '{name}' is not usable", str(e))
        return None
    except Exception as e:
        record(FAIL, f"Provider '{name}' failed to initialise",
               f"{type(e).__name__}: {e}")
        return None

    model = getattr(provider, "_model", "?")
    record(OK, f"Provider '{name}' configured", f"chat model: {model}")

    router = getattr(provider, "_router_model", None)
    if router and router != model:
        record(OK, "Router model split", f"intent routing uses: {router}")

    # A reasoning model with thinking left on is the single most common cause
    # of "the bot takes a minute to answer".
    if any(k in str(model).lower() for k in ("qwen3", "reason", "think", "r1")) \
            and not _present("OPENAI_REASONING_EFFORT"):
        record(WARN, "Model looks like a reasoning model, thinking not disabled",
               "Set OPENAI_REASONING_EFFORT=none — measured 118s vs 5.8s per reply.")
    return provider


def check_chat(provider, offline: bool) -> None:
    if provider is None or offline:
        return
    import asyncio
    try:
        reply = asyncio.run(provider.create_chat("Reply with the word: ok").send("ping"))
    except Exception as e:
        record(FAIL, "Chat call failed", f"{type(e).__name__}: {str(e)[:160]}")
        return
    if (reply or "").strip():
        record(OK, "Chat call works", f"replied {reply.strip()[:40]!r}")
    else:
        record(WARN, "Chat call returned nothing",
               "If this is a reasoning model, set OPENAI_REASONING_EFFORT=none.")


def check_embeddings(provider, offline: bool) -> None:
    if provider is None:
        return
    if not getattr(provider, "supports_embeddings", False):
        record(WARN, "Semantic memory is OFF (no embeddings route)",
               "Routers serve chat, not embeddings. Set EMBED_BASE_URL and "
               "EMBED_MODEL — a local Ollama with nomic-embed-text is free. "
               "See docs/PROVIDERS.md.")
        return
    if offline:
        record(OK, "Semantic memory configured")
        return
    import asyncio
    try:
        vec = asyncio.run(provider.embed("dimension probe"))
    except Exception as e:
        record(FAIL, "Embedding call failed", f"{type(e).__name__}: {str(e)[:160]}")
        return
    record(OK, f"Semantic memory works ({len(vec)} dimensions)")


def check_storage() -> None:
    import sqlite3
    from pathlib import Path
    path = Path(os.getenv("TELEBOT_DB") or "telebot.db")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE IF NOT EXISTS _doctor (x INTEGER)")
        con.execute("DROP TABLE _doctor")
        con.commit(); con.close()
    except Exception as e:
        record(FAIL, f"Cannot write the database at {path}",
               f"{type(e).__name__}: {e}. Set TELEBOT_DB to a writable path.")
        return
    record(OK, f"Database writable — {path}")


def check_reference_folder() -> None:
    root = os.getenv("VAULT_REF_ROOT")
    if not root:
        return
    from pathlib import Path
    p = Path(root)
    if not p.is_dir():
        record(WARN, f"VAULT_REF_ROOT is set but unreadable — {root}",
               "The bot will run without reference notes.")
        return
    record(OK, f"Reference folder readable ({len(list(p.rglob('*.md')))} notes)")


def main() -> int:
    offline = "--offline" in sys.argv
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except ImportError:
        pass

    print("telebot doctor" + ("  (offline: configuration only)" if offline else ""))
    print("-" * 58)
    check_python()
    check_dependencies()
    check_telegram(offline)
    provider = check_provider(offline)
    check_chat(provider, offline)
    check_embeddings(provider, offline)
    check_storage()
    check_reference_folder()

    fails = sum(1 for s, _, _ in results if s == FAIL)
    warns = sum(1 for s, _, _ in results if s == WARN)
    print("-" * 58)
    if fails:
        print(f"{fails} problem(s) to fix, {warns} warning(s).")
        return 1
    print(f"Ready to run. {warns} warning(s)." if warns else "Ready to run.")
    print("\nStart it with:  python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
