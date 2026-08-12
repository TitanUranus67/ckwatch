"""SQLite storage for ckwatch: schema, inserts, queries, rollups, pruning."""

from __future__ import annotations

import sqlite3
import time

from .parsing import parse_hashrate

SCHEMA = """
CREATE TABLE IF NOT EXISTS pool_snapshots (
    ts INTEGER PRIMARY KEY,
    runtime INTEGER, users INTEGER, workers INTEGER, idle INTEGER,
    disconnected INTEGER, lastupdate INTEGER,
    hashrate1m REAL, hashrate5m REAL, hashrate15m REAL, hashrate1hr REAL,
    hashrate6hr REAL, hashrate1d REAL, hashrate7d REAL,
    diff REAL, accepted REAL, rejected REAL, bestshare REAL,
    sps1m REAL, sps5m REAL, sps15m REAL, sps1h REAL
);
CREATE TABLE IF NOT EXISTS worker_snapshots (
    ts INTEGER NOT NULL,
    user TEXT NOT NULL,
    worker TEXT NOT NULL,
    hashrate1m REAL, hashrate5m REAL, hashrate1hr REAL,
    hashrate1d REAL, hashrate7d REAL,
    shares REAL, bestshare REAL, bestever REAL, lastshare INTEGER,
    PRIMARY KEY (ts, worker)
);
CREATE INDEX IF NOT EXISTS idx_worker_snapshots_ts ON worker_snapshots(ts);
CREATE TABLE IF NOT EXISTS pool_hourly (
    hour INTEGER PRIMARY KEY,
    runtime INTEGER, lastupdate INTEGER,
    hashrate1m REAL, hashrate5m REAL, hashrate15m REAL, hashrate1hr REAL,
    hashrate6hr REAL, hashrate1d REAL, hashrate7d REAL,
    diff REAL, accepted REAL, rejected REAL, bestshare REAL,
    sps1m REAL, sps5m REAL, sps15m REAL, sps1h REAL
);
CREATE TABLE IF NOT EXISTS worker_hourly (
    hour INTEGER NOT NULL,
    user TEXT NOT NULL,
    worker TEXT NOT NULL,
    hashrate1m REAL, hashrate5m REAL, hashrate1hr REAL,
    hashrate1d REAL, hashrate7d REAL,
    shares REAL, bestshare REAL, bestever REAL, lastshare INTEGER,
    PRIMARY KEY (hour, worker)
);
CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    worker TEXT,
    text TEXT,
    notified INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS best_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    worker TEXT NOT NULL,
    value REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_best_events_worker ON best_events(worker);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

POOL_AVG_COLS = [
    "hashrate1m", "hashrate5m", "hashrate15m", "hashrate1hr",
    "hashrate6hr", "hashrate1d", "hashrate7d",
    "diff", "sps1m", "sps5m", "sps15m", "sps1h",
]
POOL_MAX_COLS = ["runtime", "lastupdate", "accepted", "rejected", "bestshare"]
WORKER_AVG_COLS = ["hashrate1m", "hashrate5m", "hashrate1hr", "hashrate1d", "hashrate7d"]
WORKER_MAX_COLS = ["shares", "bestshare", "bestever", "lastshare"]

_POOL_INSERT = """
INSERT OR IGNORE INTO pool_snapshots
(ts, runtime, users, workers, idle, disconnected, lastupdate,
 hashrate1m, hashrate5m, hashrate15m, hashrate1hr, hashrate6hr, hashrate1d, hashrate7d,
 diff, accepted, rejected, bestshare, sps1m, sps5m, sps15m, sps1h)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

_WORKER_INSERT = """
INSERT OR IGNORE INTO worker_snapshots
(ts, user, worker, hashrate1m, hashrate5m, hashrate1hr, hashrate1d, hashrate7d,
 shares, bestshare, bestever, lastshare)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
"""


def connect(path: str) -> sqlite3.Connection:
    # check_same_thread=False: the app shares one connection between the
    # asyncio-to-thread collector/logtail and the API threadpool, guarded
    # by a lock in web.py.
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def pool_row(ts: int, d: dict) -> tuple:
    return (
        ts,
        d.get("runtime"), d.get("Users"), d.get("Workers"), d.get("Idle"),
        d.get("Disconnected"), d.get("lastupdate"),
        parse_hashrate(d.get("hashrate1m")), parse_hashrate(d.get("hashrate5m")),
        parse_hashrate(d.get("hashrate15m")), parse_hashrate(d.get("hashrate1hr")),
        parse_hashrate(d.get("hashrate6hr")), parse_hashrate(d.get("hashrate1d")),
        parse_hashrate(d.get("hashrate7d")),
        d.get("diff"), d.get("accepted"), d.get("rejected"), d.get("bestshare"),
        d.get("SPS1m"), d.get("SPS5m"), d.get("SPS15m"), d.get("SPS1h"),
    )


def worker_row(ts: int, user: str, worker: str, d: dict) -> tuple:
    return (
        ts, user, worker,
        parse_hashrate(d.get("hashrate1m")), parse_hashrate(d.get("hashrate5m")),
        parse_hashrate(d.get("hashrate1hr")), parse_hashrate(d.get("hashrate1d")),
        parse_hashrate(d.get("hashrate7d")),
        d.get("shares"), d.get("bestshare"), d.get("bestever"), d.get("lastshare"),
    )


def insert_pool_snapshot(conn: sqlite3.Connection, ts: int, data: dict) -> None:
    conn.execute(_POOL_INSERT, pool_row(ts, data))


def insert_user_snapshot(conn: sqlite3.Connection, ts: int, user: str, data: dict) -> list[dict]:
    """Insert rows for a user file / User: log line.

    If the data carries a "worker" array (status files), insert one row per
    worker; otherwise (log lines) insert a single row keyed by the user name.
    Also records "new best" events when a worker's best improves.

    Returns a list of {"worker", "value", "baseline"} for each new best
    event recorded (baseline = first sighting of that worker).
    """
    events = []
    workers = data.get("worker")
    if workers:
        for w in workers:
            worker = w.get("workername") or user
            conn.execute(_WORKER_INSERT, worker_row(ts, user, worker, w))
            ev = record_best(conn, ts, worker, w.get("bestshare"), w.get("bestever"))
            if ev:
                events.append(ev)
    else:
        conn.execute(_WORKER_INSERT, worker_row(ts, user, user, data))
        ev = record_best(conn, ts, user, data.get("bestshare"), data.get("bestever"))
        if ev:
            events.append(ev)
    return events


def record_best(conn: sqlite3.Connection, ts: int, worker: str,
                bestshare: float | None, bestever: float | None) -> dict | None:
    """Record an event when a worker's best difficulty exceeds anything seen
    before. The first sighting of a worker establishes the baseline.

    Returns {"worker", "value", "baseline"} when an event was recorded,
    else None."""
    value = max(bestshare or 0.0, bestever or 0.0)
    if value <= 0:
        return None
    row = conn.execute(
        "SELECT MAX(value) AS v FROM best_events WHERE worker = ?", (worker,)
    ).fetchone()
    # ckpool prints bestshare with slightly different float precision in the
    # status files vs the log lines, so the same share can differ in the ~7th
    # decimal between sources. Use a relative epsilon or such shares record
    # as near-duplicate events.
    eps = (row["v"] or 0.0) * 1e-12
    if row["v"] is None:
        is_new, baseline = True, True
    else:
        is_new, baseline = value > row["v"] + eps, False
    if is_new:
        conn.execute(
            "INSERT INTO best_events (ts, worker, value) VALUES (?,?,?)",
            (ts, worker, value),
        )
        return {"worker": worker, "value": value, "baseline": baseline}
    return None


def list_bests(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, ts, worker, value,
               (value >= (SELECT MAX(value) FROM best_events)) AS pool_record
        FROM best_events ORDER BY ts DESC, id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_pool(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM pool_snapshots ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def latest_workers(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT w.* FROM worker_snapshots w
        JOIN (SELECT worker, MAX(ts) AS mts FROM worker_snapshots GROUP BY worker) m
          ON w.worker = m.worker AND w.ts = m.mts
        ORDER BY w.worker
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _bucket(bucket: int) -> str:
    return f"(ts / {bucket}) * {bucket}"


def pool_history(conn: sqlite3.Connection, since: int, bucket: int) -> list[dict]:
    b = _bucket(bucket)
    avgs = ", ".join(f"AVG({c}) AS {c}" for c in POOL_AVG_COLS)
    maxs = ", ".join(f"MAX({c}) AS {c}" for c in POOL_MAX_COLS)
    rows = conn.execute(
        f"SELECT {b} AS ts, {avgs}, {maxs} FROM pool_snapshots"
        " WHERE ts >= ? GROUP BY ts / ? ORDER BY ts",
        (since, bucket),
    ).fetchall()
    return [dict(r) for r in rows]


def worker_history(conn: sqlite3.Connection, since: int, bucket: int) -> list[dict]:
    b = _bucket(bucket)
    avgs = ", ".join(f"AVG({c}) AS {c}" for c in WORKER_AVG_COLS)
    rows = conn.execute(
        f"SELECT {b} AS ts, worker, {avgs}, MAX(shares) AS shares,"
        " MAX(bestshare) AS bestshare, MAX(bestever) AS bestever,"
        " MAX(lastshare) AS lastshare"
        " FROM worker_snapshots WHERE ts >= ? GROUP BY ts / ?, worker ORDER BY ts",
        (since, bucket),
    ).fetchall()
    return [dict(r) for r in rows]


def pool_history_hourly(conn: sqlite3.Connection, until: int) -> list[dict]:
    """Hourly rollups older than `until` (used for the 'all' range)."""
    rows = conn.execute(
        "SELECT hour * 3600 AS ts, * FROM pool_hourly WHERE hour * 3600 < ? ORDER BY hour",
        (until,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("hour", None)
        out.append(d)
    return out


def worker_history_hourly(conn: sqlite3.Connection, until: int) -> list[dict]:
    rows = conn.execute(
        "SELECT hour * 3600 AS ts, worker, hashrate1m, hashrate5m, hashrate1hr,"
        " hashrate1d, hashrate7d, shares, bestshare, bestever, lastshare"
        " FROM worker_hourly WHERE hour * 3600 < ? ORDER BY hour",
        (until,),
    ).fetchall()
    return [dict(r) for r in rows]


def rollup(conn: sqlite3.Connection, now: int | None = None) -> None:
    """Aggregate fully-elapsed hours from raw snapshots into hourly tables."""
    now = now if now is not None else int(time.time())
    current_hour = now // 3600
    pavgs = ", ".join(f"AVG({c})" for c in POOL_AVG_COLS)
    pmaxs = ", ".join(f"MAX({c})" for c in POOL_MAX_COLS)
    conn.execute(
        f"""
        INSERT OR REPLACE INTO pool_hourly
        (hour, {", ".join(POOL_AVG_COLS)}, {", ".join(POOL_MAX_COLS)})
        SELECT ts / 3600, {pavgs}, {pmaxs}
        FROM pool_snapshots
        WHERE ts / 3600 < ?
        GROUP BY ts / 3600
        """,
        (current_hour,),
    )
    conn.execute(
        f"""
        INSERT OR REPLACE INTO worker_hourly
        (hour, user, worker, {", ".join(WORKER_AVG_COLS)}, {", ".join(WORKER_MAX_COLS)})
        SELECT ts / 3600, user, worker,
               {", ".join(f"AVG({c})" for c in WORKER_AVG_COLS)},
               {", ".join(f"MAX({c})" for c in WORKER_MAX_COLS)}
        FROM worker_snapshots
        WHERE ts / 3600 < ?
        GROUP BY ts / 3600, worker
        """,
        (current_hour,),
    )


def prune(conn: sqlite3.Connection, retention_days: int, now: int | None = None) -> int:
    """Drop raw snapshots older than the retention window. Returns rows deleted."""
    now = now if now is not None else int(time.time())
    cutoff = now - retention_days * 86400
    cur = conn.execute("DELETE FROM pool_snapshots WHERE ts < ?", (cutoff,))
    n = cur.rowcount
    cur = conn.execute("DELETE FROM worker_snapshots WHERE ts < ?", (cutoff,))
    return n + cur.rowcount


def insert_block(conn: sqlite3.Connection, ts: int | None, worker: str | None, text: str) -> None:
    conn.execute(
        "INSERT INTO blocks (ts, worker, text) VALUES (?,?,?)",
        (ts if ts is not None else int(time.time()), worker, text),
    )


def list_blocks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM blocks ORDER BY ts DESC").fetchall()
    return [dict(r) for r in rows]


def mark_block_notified(conn: sqlite3.Connection, block_id: int) -> None:
    conn.execute("UPDATE blocks SET notified = 1 WHERE id = ?", (block_id,))


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
