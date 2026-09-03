"""Things deliberately removed. This test exists so they cannot drift back.

Both were removed on purpose, and both are the kind of thing a later change
re-adds without noticing: a roast feature reads as harmless fun, and a
"more accurate" clock reads as an improvement.
"""
import os, sys, pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta
import pytz

ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = (ROOT / "main.py").read_text()

# ── the clock is local ────────────────────────────────────────────────────
# Check the code, not the prose: the docstring explaining the removal
# legitimately names the old URL.
code = "\n".join(l for l in SRC.splitlines() if not l.strip().startswith("#"))
assert ".head(" not in code, "something is HEADing a URL again"
assert "get_network_time" not in SRC, "old network clock still referenced"
assert "def now_sgt" in SRC
print("  no network clock in main.py")

os.environ.update(TELEGRAM_BOT_TOKEN="x", AI_PROVIDER="local",
                  OPENAI_BASE_URL="http://127.0.0.1:1/v1", AI_MODEL="m",
                  TELEBOT_DB="/tmp/t_hardening.db")
import main

# it must work with the network unusable, which is the point
import httpx
class Boom:
    def __init__(self, *a, **k): raise AssertionError("clock made a network call")
_real = httpx.AsyncClient
httpx.AsyncClient = Boom
try:
    t = main.now_sgt()
finally:
    httpx.AsyncClient = _real

assert t.tzinfo is not None, "timestamps must be timezone-aware"
assert str(t.tzinfo) == "Asia/Singapore", t.tzinfo
drift = abs((t - datetime.now(pytz.timezone("Asia/Singapore"))).total_seconds())
assert drift < 5, f"clock drift {drift}s"
print(f"  now_sgt() works with no network, tz-aware, drift {drift:.3f}s")

# ── emoji reactions are gone ──────────────────────────────────────────────
assert "set_reaction" not in code, "the bot is reacting again"
assert "pick_reaction" not in code and "SemanticReactor" not in code
assert not (ROOT / "reactions.py").exists(), "reactions.py is back"
# The embedding must survive: it is semantic memory, not decoration.
assert "add_embedding" in code, "semantic memory was removed with reactions"
print("  no reactions module, call site, or set_reaction")

# ── roast is gone from the group path ─────────────────────────────────────
# Again: the comment recording the removal quotes the old prompt, so assert
# against code with comments stripped.
assert 'elif action == "ROAST"' not in code, "roast handler is back"
assert "funny, punchy, authentic" not in code, "roast prompt text is back"
assert '"type": "ROAST"' not in code, "router still emits ROAST"
assert "Personality assessment of" not in code, "roast prompt is back"
print("  no roast handler, prompt, or router intent")

# the router prompt itself must not offer it
prompt_start = SRC.index("routing_prompt = ")
prompt_end = SRC.index("User message:", prompt_start)
router_prompt = SRC[prompt_start:prompt_end]
assert "ROAST" not in router_prompt, "ROAST still advertised to the model"
for intent in ("CATCHUP", "SUMMARIZE", "REMIND", "POLL", "MEMORY", "WEB_SEARCH", "CHAT"):
    assert intent in router_prompt, f"{intent} disappeared from the router"
print("  router advertises 7 intents, none of them ROAST")

# identity resolution must survive: it is used by name binding, not just roast
assert "resolve_member" in SRC, "identity resolution was removed with roast"
print("  identity binding still uses resolve_member")
print("ALL HARDENING TESTS PASSED")
