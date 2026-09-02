"""Identity: membership, aliases, fuzzy resolution, migration of old rows."""
import sys, os, tempfile, sqlite3
sys.path.insert(0, '/opt/telebot')
from store import Store

db = os.path.join(tempfile.mkdtemp(), "m.db")

# --- migration: a pre-identity database must gain user_id without losing rows
old = sqlite3.connect(db)
old.executescript("""
CREATE TABLE group_chat_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
  sender TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE group_embeddings (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
  sender TEXT NOT NULL, message TEXT NOT NULL, embedding BLOB NOT NULL, created_at TEXT NOT NULL);
""")
old.execute("INSERT INTO group_chat_logs (chat_id,sender,message,created_at) VALUES (?,?,?,?)",
            (-100, "Yuan Bing", "legacy row, no user_id", "2026-08-01T00:00:00+00:00"))
old.commit(); old.close()

s = Store(db)
assert "user_id" in {r[1] for r in s._conn.execute("PRAGMA table_info(group_chat_logs)")}
assert s._conn.execute("select count(*) from group_chat_logs").fetchone()[0] == 1
print("  migration: user_id added, legacy row preserved")
Store(db)  # migrating twice must be a no-op
print("  migration: idempotent")

C = -100
s.upsert_member(C, 1, "Yuan Bing", "yb_lim")
s.upsert_member(C, 2, "sean", None)
s.upsert_member(C, 3, "🐑💩yt ｡◕‿◕｡", None)
s.upsert_member(C, 4, "Ryan", "ryanng")

# exact / case
assert s.resolve_member(C, "sean")["user_id"] == 2
assert s.resolve_member(C, "SEAN")["user_id"] == 2
assert s.resolve_member(C, "@ryanng")["user_id"] == 4
# fuzzy across spacing -- the case LIKE could never handle
r = s.resolve_member(C, "yuanbing"); assert r["user_id"] == 1 and r["match"] in ("substring","fuzzy"), r
r = s.resolve_member(C, "Yuan  Bing"); assert r["user_id"] == 1
# near-miss spelling
r = s.resolve_member(C, "shaun"); assert r and r["user_id"] == 2, r
print("  resolve: exact, case, @username, spacing, misspelling")

# unknown name -> None (this is what triggers the teach-me path)
assert s.resolve_member(C, "Bartholomew") is None
assert s.resolve_member(C, "") is None
assert s.resolve_member(C, "   ") is None
print("  resolve: unknown -> None")

# the emoji member is unreachable by name until taught
assert s.resolve_member(C, "yt") is not None      # substring of the handle
assert s.resolve_member(C, "Marcus") is None
assert s.add_alias(C, 3, "Marcus") is True
assert s.add_alias(C, 3, "marcus") is False        # already known, case-insensitive
m = s.resolve_member(C, "Marcus"); assert m["user_id"] == 3 and m["match"] == "exact"
assert s.add_alias(C, 999, "Ghost") is False       # unknown member
print("  aliases: taught name resolves, duplicates rejected")

# ambiguity must refuse rather than guess
s.upsert_member(C, 5, "Jon", None); s.upsert_member(C, 6, "Jan", None)
assert s.resolve_member(C, "Jen") is None, s.resolve_member(C, "Jen")
print("  ambiguous fuzzy -> None, not a guess")

# rename keeps identity and aliases
s.upsert_member(C, 1, "YB", "yb_lim")
assert s.resolve_member(C, "YB")["user_id"] == 1
assert len(s.list_members(C)) == 6
print("  rename: same user_id, no duplicate row")

# messages by member, including legacy name-only rows
s.log_message(C, "Yuan Bing", "new row with id", user_id=1)
msgs = s.messages_by_member(C, 1, first_name="Yuan Bing")
assert len(msgs) == 2, msgs
print("  messages_by_member: joins id rows and legacy name rows")
print("ALL MEMBER TESTS PASSED")
