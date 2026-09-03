"""Catch-up must be bounded and honest, and identity must be fetched once."""
import os, sys, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(TELEGRAM_BOT_TOKEN="x", AI_PROVIDER="local",
                  OPENAI_BASE_URL="http://127.0.0.1:1/v1", AI_MODEL="m",
                  TELEBOT_DB="/tmp/t_cu.db")
import main

def records(n, msg="a message that is a reasonable length for a chat line"):
    return [{"sender": f"p{i % 5}", "message": f"{msg} {i}"} for i in range(n)]

# --- bounded input --------------------------------------------------------
small = main.build_catchup_prompt("Sean", records(12), "2026-09-01T00:00:00+00:00")
assert "most recent" not in small, "nothing was trimmed, so do not claim it was"
assert "12 messages" in small
print("  small backlog: sent whole, no truncation claim")

big = main.build_catchup_prompt("Sean", records(500), "2026-09-01T00:00:00+00:00")
assert len(big) < 8000, len(big)
assert "500 messages" in big, "the true total must still be stated"
assert "most recent" in big, "trimming must be disclosed to the model"
print(f"  500 messages -> {len(big)} char prompt, total stated, trimming disclosed")

# a few very long messages must also be capped, not just the count
huge = main.build_catchup_prompt("Sean", records(30, "x" * 900), "2026-09-01T00:00:00+00:00")
assert len(huge) < 9000, len(huge)
print(f"  30 huge messages -> {len(huge)} chars (character cap, not just count)")

# --- deterministic facts, not asked of the model --------------------------
recs = [{"sender": "Ryan", "message": "a"}, {"sender": "Ryan", "message": "b"},
        {"sender": "LJ", "message": "c"}]
p = main.build_catchup_prompt("Sean", recs, "x")
assert "3 messages from 2 people" in p, p[:200]
assert "Ryan (2)" in p and "LJ (1)" in p, "per-speaker counts are computed here"
print("  counts and speakers computed, not delegated to the model")

# --- asks for structure, and guards the failure mode ----------------------
for phrase in ("8 short bullets", "decisions the group actually made",
               "questions still open", "Do not invent a decision"):
    assert phrase in p, phrase
assert "Skip small talk" in p
print("  asks for bullets, decisions, open questions, and forbids inventing one")

# --- the most recent messages are the ones kept ---------------------------
numbered = [{"sender": "p", "message": f"line-{i:04d} " + "y" * 30} for i in range(400)]
tail = main.build_catchup_prompt("Sean", numbered, "x")
assert "line-0399" in tail, "the newest message must survive trimming"
assert "line-0000" not in tail, "the oldest should be the one dropped"
print("  trimming keeps the newest, drops the oldest")

# --- get_me is fetched once ----------------------------------------------
class FakeBot:
    def __init__(self): self.calls = 0
    async def get_me(self):
        self.calls += 1
        class I: username = "GeminiBrBoT"
        return I()

async def check():
    main._bot_info = None
    bot = FakeBot()
    for _ in range(25):
        info = await main.get_bot_info(bot)
        assert info.username == "GeminiBrBoT"
    return bot.calls
calls = asyncio.run(check())
assert calls == 1, f"get_me called {calls} times; should be 1"
print(f"  get_me: 25 messages -> {calls} API call")
print("ALL CATCHUP/IDENTITY TESTS PASSED")
