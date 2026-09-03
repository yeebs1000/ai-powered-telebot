"""Catch-me-up: the window is anchored per person, not a fixed size."""
import os
import sys, os, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from store import Store

s = Store(os.path.join(tempfile.mkdtemp(), "c.db"))
C = -100

def say(sender, text, uid=None):
    s.log_message(C, sender, text, uid)
    time.sleep(0.002)   # keep created_at strictly ordered

say("Ryan", "morning all", 1)
say("LJ", "yo", 2)
say("Sean", "im heading out, back later", 3)      # Sean's anchor
say("Ryan", "we should book the court for thursday", 1)
say("LJ", "8pm works", 2)
say("Ryan", "booked it", 1)

# Sean missed everything after his own last message
msgs, since = s.messages_since_user_last(C, 3, "Sean")
assert since is not None
assert [m["message"] for m in msgs] == [
    "we should book the court for thursday", "8pm works", "booked it"], msgs
print(f"  Sean missed {len(msgs)} messages since his last")

# Ryan spoke most recently, so he missed nothing
msgs, since = s.messages_since_user_last(C, 1, "Ryan")
assert since is not None and msgs == [], msgs
print("  Ryan (spoke last) -> nothing missed")

# LJ missed only what came after him
msgs, _ = s.messages_since_user_last(C, 2, "LJ")
assert [m["message"] for m in msgs] == ["booked it"], msgs
print("  LJ missed only what followed him — window differs per person")

# someone who has never posted has no anchor at all
msgs, since = s.messages_since_user_last(C, 99, "Newbie")
assert since is None and msgs == []
print("  unknown member -> no anchor (distinct from 'nothing missed')")

# legacy rows without user_id still anchor by name
s._conn.execute("INSERT INTO group_chat_logs (chat_id,sender,message,created_at)"
                " VALUES (?,?,?,?)", (C, "Ghost", "old row", "2020-01-01T00:00:00+00:00"))
s._conn.commit()
msgs, since = s.messages_since_user_last(C, 4242, "Ghost")
assert since == "2020-01-01T00:00:00+00:00"
assert len(msgs) == 6, len(msgs)
print("  pre-identity rows anchor by sender name")

# other chats never leak in
s.log_message(-999, "Ryan", "different chat entirely", 1)
msgs, _ = s.messages_since_user_last(C, 2, "LJ")
assert all("different chat" not in m["message"] for m in msgs)
print("  other chats excluded")

# the limit is respected
for i in range(20):
    say("LJ", f"spam {i}", 2)
msgs, _ = s.messages_since_user_last(C, 3, "Sean", limit=5)
assert len(msgs) == 5
print("  limit respected")
print("ALL CATCHUP TESTS PASSED")
