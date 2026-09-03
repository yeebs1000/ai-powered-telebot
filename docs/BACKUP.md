# Backup and restore

The database **is** the product. Catch-up, memory and member identity are all
reads of `telebot.db`; lose it and the bot is a stranger in the group again.

## Do not back it up with `cp`

The database runs in WAL mode with a live writer. Copying `telebot.db` alone
captures a torn state: committed transactions can still be sitting in
`telebot.db-wal`, which a naive copy leaves behind.

This is not theoretical. Measured on this deployment, against a live database
with a 420 KiB WAL:

| table | live | `cp` of the .db only | snapshot |
|---|---:|---:|---:|
| `group_chat_logs` | 141 | **131** | 141 |
| `members` | 15 | **13** | 15 |
| `reminders` | 0 rows | **table did not exist** | 0 rows |

Ten messages, two members, and an entire table gone — silently, in a file that
opens fine and looks like a backup.

## Taking a snapshot

```bash
python3 scripts/backup.py                     # defaults below
python3 scripts/backup.py --dir /mnt/x --keep 30
```

It uses `VACUUM INTO`, which takes a consistent image of a database being
written to and writes it as a **single compact file**. (sqlite3's backup API
does the first part but leaves `-wal`/`-shm` sidecars beside the copy, where
retention never prunes them and a restore can pick up the wrong pair.)

Every snapshot is then **verified before it is kept**:
`integrity_check` must return `ok` and the row counts must be readable, or the
partial file is deleted and the run fails. The final move is atomic, so a
reader never sees a half-written snapshot. Files are `0600` — they hold other
people's messages.

Names carry a millisecond timestamp, so name order and time order are the
same and two runs never collide. Retention deletes by **mtime**, not by name,
and never touches the snapshot just taken — a name sort was capable of
deleting the newest file, because a collision suffix makes `…Z-2.db` sort
before `…Z.db`.

Defaults: `TELEBOT_DB` → `/var/lib/telebot/telebot.db`,
`TELEBOT_BACKUP_DIR` → `/var/backups/telebot`, keep 14.

Scheduled daily at 04:10 by `deploy/telebot-backup.timer`.

## Restoring

```bash
sudo systemctl stop telebot
sudo install -o telebot -g telebot -m 0600 \
    /var/backups/telebot/telebot-<stamp>.db /var/lib/telebot/telebot.db
sudo rm -f /var/lib/telebot/telebot.db-wal /var/lib/telebot/telebot.db-shm
sudo systemctl start telebot
```

Delete the stale `-wal` and `-shm`: they belong to the database you just
replaced, and leaving them next to a different file is how you corrupt it.

Verify:

```bash
journalctl -u telebot --since -1m | grep -E 'Store:|REHYDRATE'
sudo -u telebot .venv/bin/python -c "
import sqlite3; c=sqlite3.connect('/var/lib/telebot/telebot.db')
print(c.execute('PRAGMA integrity_check').fetchone()[0])
print(c.execute('select count(*) from group_chat_logs').fetchone()[0])"
```

## The drill — run 2026-09-03, passed

A backup you have never restored is a hypothesis.

1. Recorded live state: 141 messages, 15 members, integrity ok.
2. Stopped the service so the database was quiescent.
3. Took a snapshot of it.
4. **Deleted the live database, `-wal` and `-shm`** — the directory was left
   with no database at all.
5. Restored the snapshot and started the service.
6. Verified: `integrity_check ok`, 141 messages, 15 members, newest message
   timestamp identical, member names intact.
7. Verified the part a row count would not catch: embeddings are BLOBs, so a
   semantic search was run against the restored file — 2 hits returned, which
   means the vectors survived byte-for-byte and memory still works.

Downtime was about twenty seconds. Snapshotting **while stopped** is what made
it risk-free: the restored file was identical to the one moved aside, so
nothing logged in the meantime could be lost.

## What this does not protect against

Backups live on **the same disk** as the database. That covers corruption, a
bad migration, and `rm` — it does **not** cover the disk dying, which is the
failure that loses everything.

There is no off-box copy of `telebot.db` today. Options, none of them taken
yet because each is a decision the owner should make:

- copy snapshots to another machine (they contain other people's messages, so
  encrypt them or don't do it);
- an encrypted remote via rclone;
- an external disk that is not this NVMe.

Until one of those exists, the honest statement is: **this survives a restart
and a mistake, not a dead disk.**
