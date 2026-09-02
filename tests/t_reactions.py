"""Reactions: the substring false positives that prompted the rewrite, plus
elongation, emoji mirroring, questions, scoring and the cooldown."""
import sys
sys.path.insert(0, '/opt/telebot')
from reactions import pick_reaction, normalize, ReactionLimiter

# --- the regressions this rewrite exists to fix ---------------------------
must_be_silent = [
    "the japan trip is booked",     # "rip" inside "trip"
    "gripping match last night",
    "see the description",
    "scripted drama",
    "that costs 1000 dollars",      # "100" inside "1000"
    "i bought new gloves",          # "love" inside "gloves"
    "clover field",
    "shipping arrives friday",
    "principle of the thing",
    "my toothbrush cost <$100",     # the real false positive from the logs
]
for t in must_be_silent:
    got = pick_reaction(t)
    assert got is None, f"{t!r} should be silent, got {got}"
print(f"  {len(must_be_silent)} substring false positives now silent")

# --- genuine matches still fire ------------------------------------------
cases = [
    ("lol that's mad", "🤣"), ("HAHAHAHA", "🤣"), ("lmaooooo", "🤣"),
    ("congrats bro!", "🎉"), ("we won!!", "🎉"),
    ("rip my wallet", "😢"), ("that sucks man", "😢"),
    ("thanks a lot", "🙏"), ("thank you!", "🙏"),
    ("facts", "💯"), ("spot on", "💯"),
    ("i love this", "❤"), ("absolutely beautiful", "❤"),
    ("no way", "🤯"), ("wow", "🤯"),
    ("lets go!!!", "🔥"), ("that's insane", "🔥"),
    ("sounds good", "🤝"), ("interesting", "👀"),
]
for text, want in cases:
    got = pick_reaction(text)
    assert got == want, f"{text!r} -> {got}, wanted {want}"
print(f"  {len(cases)} genuine triggers fire correctly (incl. elongation)")

# --- elongation ----------------------------------------------------------
assert normalize("sooooo") == "soo"
# Alternating letters have no run of 3+ to collapse — laughter is matched by
# its own pattern instead, which is why _LAUGHTER exists.
assert normalize("HAHAHAHAHA") == "hahahahaha"
assert pick_reaction("HAHAHAHAHA") == "🤣"
assert pick_reaction("thankssss") == "🙏"
print("  elongation collapsed before matching")

# --- emoji-only messages are mirrored ------------------------------------
assert pick_reaction("😂") == "🤣"
assert pick_reaction("😭😭😭") == "😢"
assert pick_reaction("🔥🔥") == "🔥"
assert pick_reaction("🫠") is None      # not mirrorable, stay quiet
print("  emoji-only messages mirrored")

# --- questions are left alone --------------------------------------------
assert pick_reaction("is that insane or what?") is None
assert pick_reaction("congrats?") is None
print("  questions get no reaction")

# --- scoring beats list order --------------------------------------------
# "congrats" sits below "lol" in the table; more evidence should still win.
assert pick_reaction("lol congrats congratulations well done") == "🎉"
print("  strongest category wins, not the highest rule")

# --- edge cases ----------------------------------------------------------
for junk in ["", "   ", None, "\n"]:
    assert pick_reaction(junk) is None
print("  empty/None input safe")

# --- cooldown ------------------------------------------------------------
lim = ReactionLimiter(cooldown_seconds=45)
assert lim.allow(-100, now=1000.0) is True
assert lim.allow(-100, now=1010.0) is False      # too soon
assert lim.allow(-200, now=1010.0) is True       # different chat, independent
assert lim.allow(-100, now=1050.0) is True       # cooldown elapsed
print("  cooldown is per-chat and time-based")
print("ALL REACTION TESTS PASSED")
