import os
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from store import Store
from datetime import datetime, timedelta
import pytz
TZ = pytz.timezone("Asia/Singapore")

root = tempfile.mkdtemp()
db = os.path.join(root, "t.db")
os.environ["YEEBS_AI_ROOT"] = os.path.join(root, "13 AI")
os.environ["TELEBOT_DB"] = db
os.environ["TELEBOT_VAULT_RECAP"] = "0"   # deterministic sections only for this test

s = Store(db)
s_store = s
day = (datetime.now(TZ) - timedelta(days=1))
# hand-write created_at so rows land in yesterday's SGT window
import sqlite3
base = TZ.localize(datetime(day.year, day.month, day.day, 14, 0)).astimezone(pytz.utc)
rows = [(-100,"alice","pipeline broke again, deploy pipeline is cursed"),
        (-100,"bob","i can fix the pipeline tomorrow"),
        (-100,"alice","sushi after? sushi place near office"),
        (-200,"carol","different chat entirely")]
for i,(c,sender,msg) in enumerate(rows):
    s._conn.execute("INSERT INTO group_chat_logs (chat_id,sender,message,created_at) VALUES (?,?,?,?)",
                    (c,sender,msg,(base+timedelta(minutes=i)).isoformat()))
s._conn.commit()

import project_vault as pv
pv.AI_ROOT = __import__('pathlib').Path(os.environ["YEEBS_AI_ROOT"])
pv.OWNED_DIR = pv.AI_ROOT / "Telegram" / "Daily"
pv.INDEX = pv.AI_ROOT / "Telegram" / "Telegram Daily Index.md"
pv.DB = db

p = pv.render_day(s, day.replace(tzinfo=None)); pv.render_index()
text = p.read_text()
print(text)
assert "classification: local_only" in text
assert "**4** messages across **2** chat(s)" in text, "count/chat line"
assert "**3** participants" in text
assert "`pipeline`" in text and "`sushi`" in text, "recurring terms"
assert "the" not in text.split("Recurring terms")[1].split("##")[0], "stopword leaked"
assert "## Recap" not in text, "recap must be absent when disabled"

# human block preservation + idempotency
head, rest = text.split(pv.HUMAN_START, 1)
_, tail = rest.split(pv.HUMAN_END, 1)
text2 = head + pv.HUMAN_START + "\nMY OWN NOTE\n" + pv.HUMAN_END + tail
p.write_text(text2)
pv.render_day(s, day.replace(tzinfo=None))
assert "MY OWN NOTE" in p.read_text(), "human block was clobbered"
a = p.read_text()
pv.render_day(s, day.replace(tzinfo=None))
b = p.read_text()
import re
strip = lambda t: re.sub(r"generated: .*", "", t)
assert strip(a) == strip(b), "not idempotent"
for _ in range(3):
    pv.render_day(s_store, day.replace(tzinfo=None))
final = p.read_text()
assert final.count("MY OWN NOTE") == 1, "human block duplicated"
assert strip(final) == strip(b), "human block grew across runs"
assert "no messages" not in text
# empty day writes nothing
assert pv.render_day(s, datetime(2001,1,1)) is None
print("ALL PROJECTOR TESTS PASSED")
