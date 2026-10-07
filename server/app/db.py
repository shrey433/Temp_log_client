import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    fw_version  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS readings (
    device_id TEXT NOT NULL,
    ts        TEXT NOT NULL,           -- ISO 8601 UTC, e.g. 2026-09-09T14:32:10Z
    PRIMARY KEY (device_id, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS channel_values (
    device_id TEXT NOT NULL,
    ts        TEXT NOT NULL,
    ch        INTEGER NOT NULL,
    type      TEXT NOT NULL,
    temp_c    REAL,                    -- NULL when the MAX31865 reported a fault
    PRIMARY KEY (device_id, ts, ch),
    FOREIGN KEY (device_id, ts) REFERENCES readings (device_id, ts)
) WITHOUT ROWID;
"""


class Database:
    """Small SQLite wrapper. One connection guarded by a lock is plenty for a 10 s/device cadence."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def store(self, device_id: str, fw_version: str, now: str, rows: list[dict]) -> tuple[int, int]:
        """Insert rows atomically. Returns (accepted, duplicates); dedupe key is device_id + ts."""
        accepted = duplicates = 0
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO devices (device_id, fw_version, last_seen) VALUES (?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET fw_version=excluded.fw_version, "
                "last_seen=excluded.last_seen",
                (device_id, fw_version, now),
            )
            for row in rows:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO readings (device_id, ts) VALUES (?, ?)",
                    (device_id, row["ts"]),
                )
                if cur.rowcount == 0:
                    duplicates += 1
                    continue
                self._conn.executemany(
                    "INSERT INTO channel_values (device_id, ts, ch, type, temp_c) VALUES (?, ?, ?, ?, ?)",
                    [(device_id, row["ts"], c["ch"], c["type"], c["temp_c"]) for c in row["channels"]],
                )
                accepted += 1
        return accepted, duplicates

    def devices(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT device_id, fw_version, last_seen FROM devices ORDER BY device_id")
            return [dict(r) for r in cur]

    def readings(self, device_id: str, since: str | None, limit: int) -> list[dict]:
        """Newest-first rows, each with its 8 channels in order."""
        with self._lock:
            q = "SELECT ts FROM readings WHERE device_id = ?"
            args: list = [device_id]
            if since:
                q += " AND ts >= ?"
                args.append(since)
            q += " ORDER BY ts DESC LIMIT ?"
            args.append(limit)
            out = []
            for r in self._conn.execute(q, args).fetchall():
                chans = self._conn.execute(
                    "SELECT ch, type, temp_c FROM channel_values WHERE device_id = ? AND ts = ? ORDER BY ch",
                    (device_id, r["ts"]),
                ).fetchall()
                out.append({"ts": r["ts"], "channels": [dict(c) for c in chans]})
            return out
