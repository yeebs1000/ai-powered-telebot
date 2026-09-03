"""Scoped vault reference: matching, deny-skipping, caching, absence."""
import os
import sys, os, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vault import VaultReference

root = tempfile.mkdtemp()
def note(name, text, classification="local_only"):
    open(os.path.join(root, name), "w").write(
        f"---\ntype: note\nclassification: {classification}\n---\n\n{text}")

note("Badminton Sessions.md",
     "We play badminton at Bishan every Thursday 8pm. Court 4. Ryan books it.")
note("Group Trip Japan.md",
     "Planning Osaka in March. Budget around 2000 sgd. Sean wants Universal Studios.")
note("Private Salary Notes.md", "Do not share this anywhere.", classification="deny")

v = VaultReference(root)
assert v.enabled

# deny is skipped even inside the allowed folder
v._load()
titles = {n["title"] for n in v._cache}
assert "Private Salary Notes" not in titles, titles
assert len(v._cache) == 2
print("  loaded", len(v._cache), "notes; 'deny' skipped")

# topical retrieval
r = v.search("when is badminton this week")
assert r and r[0]["title"] == "Badminton Sessions", r
print(f"  'when is badminton'      -> {r[0]['title']} ({r[0]['score']})")
r = v.search("what are we doing about japan")
assert r and r[0]["title"] == "Group Trip Japan", r
print(f"  'what about japan'       -> {r[0]['title']} ({r[0]['score']})")

# unrelated chatter must not drag a note in
assert v.search("lol ok") == []
assert v.search("hows everything bro") == []
print("  unrelated chatter        -> no reference pulled")

# deny content never reaches a prompt
blk = v.context_block("salary")
assert "Do not share" not in blk
print("  deny content unreachable via context_block")

blk = v.context_block("badminton court booking")
assert "Bishan" in blk and blk.startswith("[Reference notes")
print("  context_block builds a labelled block")

# cache invalidates when a note changes
before = len(v._cache)
time.sleep(1.1)
note("New Topic.md", "Fantasy league draft is on Sunday.")
assert v.search("fantasy league draft")[0]["title"] == "New Topic"
assert len(v._cache) == before + 1
print("  cache picks up a new note")

# absent / unconfigured folder degrades silently
assert VaultReference(None).context_block("anything") == ""
assert VaultReference("/nonexistent").search("anything") == []
assert not VaultReference("/nonexistent").enabled
print("  missing folder -> disabled, no crash")
print("ALL VAULT TESTS PASSED")
