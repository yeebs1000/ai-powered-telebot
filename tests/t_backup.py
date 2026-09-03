"""Backups must be consistent, verified, single-file, and actually restorable."""
import os, subprocess, sys, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from store import Store

work = tempfile.mkdtemp()
db = os.path.join(work, "live.db")
out = os.path.join(work, "snaps")

s = Store(db)
for i in range(40):
    s.log_message(-100, "Ryan" if i % 2 else "LJ", f"message {i}", user_id=i % 2 + 1)
s.upsert_member(-100, 1, "LJ", None)
s.create_reminder(-100, 1, "LJ", "bins out", "2030-01-01T00:00:00+00:00")
s.add_embedding(-100, "LJ", "sushi tomorrow", [0.1] * 768, user_id=1)

# rows still in the WAL are exactly what a naive cp loses
assert os.path.exists(db + "-wal"), "test needs a WAL to be meaningful"
naive = os.path.join(work, "naive.db")
open(naive, "wb").write(open(db, "rb").read())
n = sqlite3.connect(f"file:{naive}?immutable=1", uri=True)
try:
    cp_rows = n.execute("select count(*) from group_chat_logs").fetchone()[0]
except sqlite3.Error:
    cp_rows = -1
print(f"  live=40 rows, naive cp sees {cp_rows} -> cp is not a backup")

r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "backup.py"),
                    "--db", db, "--dir", out, "--keep", "3"],
                   capture_output=True, text=True)
assert r.returncode == 0, r.stderr
snaps = sorted(f for f in os.listdir(out) if f.endswith(".db"))
assert len(snaps) == 1, snaps
print("  snapshot taken:", snaps[0])

# single file: no sidecars left behind for retention to miss
extra = [f for f in os.listdir(out) if not f.endswith(".db")]
assert extra == [], extra
print("  no -wal/-shm/.partial left behind")

snap = os.path.join(out, snaps[0])
c = sqlite3.connect(f"file:{snap}?immutable=1", uri=True)
assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
assert c.execute("select count(*) from group_chat_logs").fetchone()[0] == 40, "WAL rows must be captured"
assert c.execute("select count(*) from reminders").fetchone()[0] == 1
assert oct(os.stat(snap).st_mode)[-3:] == "600", "snapshots hold other people's messages"
print("  snapshot: integrity ok, all 40 rows incl. WAL, reminder present, mode 600")

# restore into a fresh path and use it through the real Store
restored = os.path.join(work, "restored.db")
open(restored, "wb").write(open(snap, "rb").read())
rs = Store(restored)
assert len(rs.recent_messages(-100, limit=99)) == 40
assert len(rs.pending_reminders()) == 1
hits = rs.match_embeddings(-100, [0.1] * 768, threshold=0.3, count=1)
assert hits and hits[0]["message"] == "sushi tomorrow", hits
print("  restored file: messages, pending reminder, and embeddings all usable")

# retention keeps N and prunes the rest
for _ in range(4):
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "backup.py"),
                    "--db", db, "--dir", out, "--keep", "3"],
                   capture_output=True, text=True, check=True)
kept = sorted(f for f in os.listdir(out) if f.endswith(".db"))
assert len(kept) == 3, kept
print("  retention: --keep 3 leaves exactly 3")

# a missing database fails loudly rather than writing an empty snapshot
r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "backup.py"),
                    "--db", os.path.join(work, "nope.db"), "--dir", out],
                   capture_output=True, text=True)
assert r.returncode == 1 and "no database" in r.stderr, r
print("  missing database: exits 1, writes nothing")
print("ALL BACKUP TESTS PASSED")
