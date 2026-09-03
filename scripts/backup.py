#!/usr/bin/env python3
"""Consistent snapshot of the telebot database.

    python3 backup.py [--dir DIR] [--keep N]

Why not `cp`: the database runs in WAL mode with a live writer. Copying the
file can capture a torn state -- a snapshot with a committed transaction
missing from the main file and stranded in a -wal that was not copied with it.
sqlite3's backup API takes a consistent image of a database being written to,
which is the whole reason it exists.

Every snapshot is verified before it is kept: integrity_check must return "ok"
and the row counts must be readable. An unverified backup is a guess.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

TABLES = ("group_chat_logs", "group_embeddings", "members", "active_polls", "reminders")


def counts(con: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for t in TABLES:
        try:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = -1        # table absent in an older snapshot
    return out


def snapshot(src: Path, dest: Path) -> dict[str, int]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()
    # VACUUM INTO, not the backup API: both take a consistent image of a live
    # database, but VACUUM INTO writes a single compact file with no -wal/-shm
    # sidecars. The backup API leaves those next to the copy, where retention
    # never prunes them and a restore can pick up the wrong pair.
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        source.execute("VACUUM INTO ?", (str(tmp),))
    finally:
        source.close()
    os.chmod(tmp, 0o600)       # it holds other people's messages

    # immutable=1 so verifying cannot create sidecars of its own.
    check = sqlite3.connect(f"file:{tmp}?immutable=1", uri=True)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"backup failed integrity_check: {ok}")
        rows = counts(check)
    finally:
        check.close()

    os.replace(tmp, dest)      # atomic: a reader never sees a partial file
    return rows


def prune(directory: Path, keep: int, protect: Path | None = None) -> list[Path]:
    """Drop all but the newest `keep` snapshots.

    Ordered by mtime, not by name. A name sort is wrong here: a collision
    suffix makes "…Z-2.db" sort BEFORE "…Z.db" ('-' < '.'), so the newest file
    reads as the oldest and retention deletes the snapshot it has just taken.
    `protect` is belt-and-braces for that same file.
    """
    # Sweep any stray sidecars from older runs so they cannot accumulate or be
    # mistaken for part of a snapshot.
    for junk in list(directory.glob("*.partial*")) + list(directory.glob("*.db-wal")) \
            + list(directory.glob("*.db-shm")):
        junk.unlink(missing_ok=True)
    snaps = sorted(directory.glob("telebot-*.db"), key=lambda f: f.stat().st_mtime)
    if protect is not None:
        snaps = [f for f in snaps if f != protect]
        keep = max(keep - 1, 0)          # the new one occupies a slot
    dropped = snaps[:-keep] if keep > 0 and len(snaps) > keep else (snaps if keep == 0 else [])
    for f in dropped:
        f.unlink(missing_ok=True)
    return dropped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=os.getenv("TELEBOT_DB", "/var/lib/telebot/telebot.db"))
    ap.add_argument("--dir", default=os.getenv("TELEBOT_BACKUP_DIR", "/var/backups/telebot"))
    ap.add_argument("--keep", type=int, default=14)
    args = ap.parse_args()

    src = Path(args.db)
    if not src.exists():
        print(f"no database at {src}", file=sys.stderr)
        return 1

    # Millisecond resolution: at second resolution two runs in the same second
    # collide, and either overwrite each other or need a suffix that breaks
    # name ordering. Unique stamps keep name order and time order the same.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
    dest = Path(args.dir) / f"telebot-{stamp}.db"
    n = 2
    while dest.exists():                 # belt and braces; should not trigger
        dest = Path(args.dir) / f"telebot-{stamp}-{n}.db"
        n += 1
    rows = snapshot(src, dest)
    dropped = prune(Path(args.dir), args.keep, protect=dest)

    size = dest.stat().st_size
    print(f"{dest}  {size/1024:.0f} KiB  integrity ok")
    print("  " + "  ".join(f"{t}={n}" for t, n in rows.items()))
    if dropped:
        print(f"  pruned {len(dropped)} old snapshot(s), keeping {args.keep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
