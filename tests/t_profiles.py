"""Style profiles: distinctiveness, thresholds, habits, caching, thin data."""
import sys, os, tempfile
sys.path.insert(0, '/opt/telebot')
from store import Store
from profiles import ProfileBuilder

s = Store(os.path.join(tempfile.mkdtemp(), "p.db"))
C = -100

# Everyone talks about badminton; only Ryan bangs on about the shuttle brand.
for i in range(12):
    s.log_message(C, "Ryan", f"badminton yonex shuttle again yonex {i}")
for i in range(12):
    s.log_message(C, "LJ", f"badminton tonight sure {i}")
for i in range(10):
    s.log_message(C, "Ng", f"BADMINTON LETS GO {i}".upper())
for i in range(8):
    s.log_message(C, "Sean", f"badminton where ah? what time? {i}")
for i in range(3):
    s.log_message(C, "Rare", "hello")          # below MIN_MESSAGES

pb = ProfileBuilder(s)
d = pb.build(C)
names = {m["name"] for m in d["members"]}
assert names == {"Ryan", "LJ", "Ng", "Sean"}, names
assert "Rare" not in names, "members under the threshold must not be profiled"
print("  profiles built for 4 members; sparse member excluded")

ryan = next(m for m in d["members"] if m["name"] == "Ryan")
assert "yonex" in ryan["words"], ryan["words"]
# "badminton" is said by everyone, so it is not distinctive of anyone
for m in d["members"]:
    assert "badminton" not in m["words"], (m["name"], m["words"])
print("  distinctive vocab found ('yonex'); shared word excluded from everyone")

assert "badminton" in d["group_words"]
print("  shared vocab surfaces at group level:", d["group_words"][:4])

ng = next(m for m in d["members"] if m["name"] == "Ng")
assert ng["caps_rate"] >= 0.9, ng["caps_rate"]
assert "caps" in pb._habits(ng)
sean = next(m for m in d["members"] if m["name"] == "Sean")
assert sean["question_rate"] >= 0.9, sean["question_rate"]
assert "questions" in pb._habits(sean)
print("  habits detected: caps for Ng, questions for Sean")

blk = pb.group_block(C)
assert blk.startswith("[How this group talks")
assert "yonex" in blk and "Ryan" in blk
print("  group_block renders")

mb = pb.member_block(C, "ryan")
assert "Ryan" in mb and "yonex" in mb
assert pb.member_block(C, "Nobody") == ""
print("  member_block renders, unknown member -> empty")

# caching
first = pb.get(C)
s.log_message(C, "Ryan", "brand new message about squash")
assert pb.get(C) is first, "cached result should be reused inside the TTL"
pb.invalidate(C)
assert pb.get(C) is not first
print("  cache holds within TTL and clears on invalidate")

# thin / empty data must produce nothing rather than noise
empty = Store(os.path.join(tempfile.mkdtemp(), "e.db"))
pb2 = ProfileBuilder(empty)
assert pb2.group_block(-1) == ""
assert pb2.member_block(-1, "anyone") == ""
assert pb2.build(-1)["members"] == []
print("  empty history -> empty blocks, no fabricated profile")
print("ALL PROFILE TESTS PASSED")
