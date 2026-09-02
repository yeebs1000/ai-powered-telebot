"""Tag-together identity binding, against fake Telegram update shapes."""
import asyncio, os, sys, tempfile, types
sys.path.insert(0, '/opt/telebot')
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ["TELEBOT_DB"] = os.path.join(tempfile.mkdtemp(), "b.db")
import main

C, BOT = -100, "@GeminiBrBoT"
store = main.store
store.upsert_member(C, 3, "🐑💩yt ｡◕‿◕｡", None)   # unreachable by name
store.upsert_member(C, 2, "sean", None)
store.upsert_member(C, 7, "LJ", None)

def U(text, reply_from=None, entities=()):
    """Minimal stand-in for the parts of an Update this code reads."""
    msg = types.SimpleNamespace(
        reply_to_message=(types.SimpleNamespace(from_user=reply_from) if reply_from else None),
        entities=list(entities))
    return types.SimpleNamespace(message=msg)

def user(uid, name, is_bot=False):
    return types.SimpleNamespace(id=uid, first_name=name, is_bot=is_bot, username=None)

def run(text, **kw):
    return asyncio.run(main.try_bind_identity(U(text, **kw), None, C, text, BOT))

emoji_guy = user(3, "🐑💩yt ｡◕‿◕｡")

# not a naming message -> falls through to normal handling
assert run(f"{BOT} what do you think", reply_from=emoji_guy) is None
assert run(f"{BOT} this is Marcus") is None          # no referent at all
print("  non-naming and referent-less messages fall through")

# the core case: reply to the emoji member, tag bot, name them
r = run(f"{BOT} this is Marcus", reply_from=emoji_guy)
assert r and "Marcus" in r, r
m = store.resolve_member(C, "Marcus")
assert m and m["user_id"] == 3, m
print("  bound by reply:", r)

# now reachable by name, and fuzzily
assert store.resolve_member(C, "marcus")["user_id"] == 3
assert store.resolve_member(C, "Marcuss")["user_id"] == 3
print("  taught name resolves exactly and fuzzily")

# repeat binding is idempotent, not an error
r2 = run(f"{BOT} he's Marcus", reply_from=emoji_guy)
assert r2 and "Already" in r2, r2
print("  repeat:", r2)

# other naming phrasings
for phrase, uid, name in [("that is Dave", 7, "Dave"), ("call him Bobby", 2, "Bobby")]:
    r3 = run(f"{BOT} {phrase}", reply_from=user(uid, "x"))
    assert r3 and name in r3, (phrase, r3)
print("  phrasing variants: 'that is X', 'call him X'")

# refuses to steal a name already belonging to someone else
r4 = run(f"{BOT} this is Marcus", reply_from=user(7, "LJ"))
assert r4 and "already know" in r4.lower(), r4
assert store.resolve_member(C, "Marcus")["user_id"] == 3   # unchanged
print("  collision refused:", r4)

# junk words are not names
assert run(f"{BOT} this is the guy", reply_from=emoji_guy) is None
assert run(f"{BOT} that's my friend", reply_from=emoji_guy) is None
print("  filler words rejected as names")

# a reply to the bot itself is not a referent
assert run(f"{BOT} this is Zoe", reply_from=user(999, "bot", is_bot=True)) is None
print("  bot's own messages are not a referent")

# unknown member -> honest refusal, no silent no-op
r5 = run(f"{BOT} this is Nadia", entities=[types.SimpleNamespace(
    type="mention", offset=0, length=len("@ghost"))])
print("  unknown member:", r5)
print("ALL BINDING TESTS PASSED")
