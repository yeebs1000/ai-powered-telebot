"""Promises must survive a restart.

The failure this prevents: the bot says "✅ I'll remind you at 9", the process
restarts, and the reminder is gone with no trace. The user was already told it
was done, which makes it a broken promise rather than a lost task.

"Restart" here is a fresh Store over the same file plus a fake job queue,
which is exactly what the process does on boot.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from datetime import datetime, timedelta
import pytz
from store import Store

DB = os.path.join(tempfile.mkdtemp(), "p.db")
UTC = pytz.utc
s = Store(DB)
now = datetime.now(UTC)

# --- reminders survive, and only the pending ones come back ---------------
past = s.create_reminder(-100, 1, "Ryan", "take the bins out",
                         (now - timedelta(hours=2)).isoformat())
soon = s.create_reminder(-100, 2, "LJ", "call the court",
                         (now + timedelta(minutes=30)).isoformat())
done = s.create_reminder(-100, 3, "Sean", "already handled",
                         (now - timedelta(days=1)).isoformat())
s.mark_reminder_fired(done)

reopened = Store(DB)                      # <- the restart
pending = reopened.pending_reminders()
ids = [r["id"] for r in pending]
assert ids == [past, soon], ids           # ordered by due_at, fired one excluded
assert pending[0]["text"] == "take the bins out"
assert pending[0]["user_name"] == "Ryan"
print(f"  {len(pending)} pending reminders survived; the fired one did not come back")

# --- rehydrate schedules them, firing overdue ones immediately ------------
class FakeQueue:
    def __init__(self): self.jobs = []
    def run_once(self, cb, when, data): self.jobs.append((cb.__name__, when, data))

class FakeApp:
    def __init__(self): self.job_queue = FakeQueue()

os.environ.update(TELEGRAM_BOT_TOKEN="x", AI_PROVIDER="local",
                  OPENAI_BASE_URL="http://127.0.0.1:1/v1", AI_MODEL="m",
                  TELEBOT_DB=DB)
import main
main.store = reopened

app = FakeApp()
n_rem, n_poll = asyncio.run(main.rehydrate(app))
assert n_rem == 2, n_rem
names = [j[0] for j in app.job_queue.jobs]
assert names.count("execute_dynamic_reminder") == 2, names

overdue = [j for j in app.job_queue.jobs if j[2].get("reminder_id") == past][0]
assert overdue[1] == 0, "an overdue reminder must fire immediately, not be dropped"
assert "late_since" in overdue[2], "a late reminder must say it is late"
future = [j for j in app.job_queue.jobs if j[2].get("reminder_id") == soon][0]
assert future[1] != 0 and "late_since" not in future[2]
print("  overdue fires now and is labelled late; future keeps its own time")

# --- polls: expiry survives too, and needs the message id ----------------
s2 = Store(DB)
s2.create_poll("p_live", -100, "Lunch?", {"a": 0, "b": 0},
               (now + timedelta(minutes=3)).isoformat())
s2.set_poll_message("p_live", 4242)
s2.create_poll("p_stale", -100, "Old?", {"a": 0},
               (now - timedelta(minutes=9)).isoformat())
s2.set_poll_message("p_stale", 4243)
s2.create_poll("p_orphan", -100, "No message", {"a": 0},
               (now + timedelta(minutes=3)).isoformat())   # never sent

main.store = Store(DB)
app2 = FakeApp()
_, n_poll = asyncio.run(main.rehydrate(app2))
assert n_poll == 2, n_poll
polls = {j[2]["poll_id"]: j for j in app2.job_queue.jobs if j[0] == "expire_poll_job"}
assert polls["p_stale"][1] == 0, "an expired poll must be closed on startup"
assert polls["p_live"][1] != 0
assert polls["p_live"][2]["message_id"] == 4242
assert "p_orphan" not in polls
assert main.store.get_poll("p_orphan") is None, "a poll with no message must be dropped, not left"
print("  polls: live rescheduled, expired closed now, orphan row removed")

# --- rehydrating twice must not double-fire ------------------------------
main.store.mark_reminder_fired(past)
main.store.mark_reminder_fired(soon)
app3 = FakeApp()
n_rem3, _ = asyncio.run(main.rehydrate(app3))
assert n_rem3 == 0, n_rem3
print("  once fired, a reminder is not rescheduled again")

# --- an empty store rehydrates cleanly -----------------------------------
main.store = Store(os.path.join(tempfile.mkdtemp(), "empty.db"))
assert asyncio.run(main.rehydrate(FakeApp())) == (0, 0)
print("  empty store: no jobs, no error")
print("ALL PERSISTENCE TESTS PASSED")
