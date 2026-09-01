import sys, os, tempfile
sys.path.insert(0, '/opt/telebot')
import numpy as np
from store import Store

db = os.path.join(tempfile.mkdtemp(), "t.db")
s = Store(db)

# logs: order + limit
for i in range(10):
    s.log_message(1, "alice" if i % 2 else "Bob", f"msg{i}")
r = s.recent_messages(1, limit=3)
assert [x["message"] for x in r] == ["msg7", "msg8", "msg9"], r
assert s.recent_messages(999) == []
# sender match is case-insensitive substring
assert len(s.messages_by_sender(1, "BO")) == 5
assert len(s.messages_by_sender(1, "nobody")) == 0

# embeddings: cosine ranking + threshold
s.add_embedding(1, "alice", "cats", [1.0, 0.0, 0.0])
s.add_embedding(1, "bob",   "dogs", [0.0, 1.0, 0.0])
s.add_embedding(1, "carol", "kitten", [0.9, 0.1, 0.0])
m = s.match_embeddings(1, [1.0, 0.0, 0.0], threshold=0.3, count=5)
assert [x["message"] for x in m] == ["cats", "kitten"], m
assert m[0]["similarity"] > m[1]["similarity"]
assert s.match_embeddings(1, [0.0, 0.0, 1.0], threshold=0.3) == []
# a dimension change must not raise
s.add_embedding(1, "dave", "old-model", [1.0] * 768)
assert [x["message"] for x in s.match_embeddings(1, [1.0, 0.0, 0.0])] == ["cats", "kitten"]
assert s.match_embeddings(1, [0.0, 0.0, 0.0]) == []

# polls: default votes, vote change, delete
s.create_poll("p1", 1, "Lunch?", {"pizza": 0, "sushi": 0}, "2026-01-01T00:00:00+00:00")
p = s.get_poll("p1")
assert p["votes"] == {} and p["options"] == {"pizza": 0, "sushi": 0}, p
s.update_poll("p1", {"pizza": 1, "sushi": 0}, {"u1": "pizza"})
assert s.get_poll("p1")["votes"] == {"u1": "pizza"}
s.delete_poll("p1")
assert s.get_poll("p1") is None
assert s.get_poll("nope") is None

# projector read window
rows = s.messages_in_range("0000", "9999")
assert len(rows) == 10 and rows[0]["message"] == "msg0"
assert s.messages_in_range("9998", "9999") == []

print("ALL STORE TESTS PASSED")
