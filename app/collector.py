"""Collector: periodic snapshot of ckpool status files into SQLite."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time

from . import db
from .config import Config
from .parsing import parse_pool_status, parse_user_status

log = logging.getLogger("ckwatch.collector")

STATUS_ACTIVE_S = 300
STATUS_IDLE_S = 1800


def worker_status(lastshare: int | None, now: int) -> str:
    age = now - (lastshare or 0)
    if age < STATUS_ACTIVE_S:
        return "active"
    if age < STATUS_IDLE_S:
        return "idle"
    return "offline"


def collect_once(conn, cfg: Config, now: int | None = None, on_best=None) -> bool:
    """Read pool.status + users/* and insert one snapshot. Returns True on success.

    on_best(worker, value) is called for each genuine new best (first sightings
    are baselines and do not fire)."""
    now = now if now is not None else int(time.time())
    pool_path = os.path.join(cfg.log_dir, "pool", "pool.status")
    users_dir = os.path.join(cfg.log_dir, "users")
    inserted = False

    try:
        with open(pool_path, encoding="utf-8") as f:
            pool = parse_pool_status(f.read())
        db.insert_pool_snapshot(conn, now, pool)
        inserted = True
    except FileNotFoundError:
        log.warning("pool.status not found at %s", pool_path)
    except Exception:
        log.exception("failed to read pool.status")

    try:
        names = sorted(os.listdir(users_dir))
    except FileNotFoundError:
        names = []
        log.warning("users dir not found at %s", users_dir)
    for name in names:
        if name.endswith(".status"):
            continue
        path = os.path.join(users_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                user = parse_user_status(f.read())
            events = db.insert_user_snapshot(conn, now, name, user)
            inserted = True
            if on_best:
                for ev in events:
                    if not ev["baseline"]:
                        on_best(ev["worker"], ev["value"])
        except Exception:
            log.exception("failed to read user file %s", path)

    conn.commit()
    return inserted


def _guarded(lock: threading.Lock, fn, *args):
    with lock:
        return fn(*args)


def _check_status_transitions(conn, prev_status: dict | None, on_status) -> dict:
    """Compare current worker statuses against the previous poll; fire
    on_status(worker, status) on transitions into/out of "offline".
    The first poll only seeds the baseline."""
    now = int(time.time())
    current = {w["worker"]: worker_status(w.get("lastshare"), now)
               for w in db.latest_workers(conn)}
    if prev_status is not None:
        for worker, status in current.items():
            prev = prev_status.get(worker)
            if prev == status:
                continue
            if status == "offline" or prev == "offline":
                on_status(worker, status)
    return current


async def collector_loop(conn, cfg: Config, lock: threading.Lock | None = None,
                         on_best=None, on_status=None) -> None:
    """Snapshot every cfg.poll_interval seconds; rollup+prune periodically."""
    log.info("collector started (interval=%ss, log_dir=%s)", cfg.poll_interval, cfg.log_dir)
    lock = lock or threading.Lock()
    last_rollup = 0.0
    prev_status: dict[str, str] | None = None
    while True:
        try:
            await asyncio.to_thread(_guarded, lock, collect_once,
                                    conn, cfg, None, on_best)
        except Exception:
            log.exception("collector iteration failed")
        if on_status is not None:
            try:
                prev_status = await asyncio.to_thread(
                    _guarded, lock, _check_status_transitions, conn, prev_status, on_status)
            except Exception:
                log.exception("status transition check failed")
        now = time.time()
        if now - last_rollup >= cfg.rollup_interval:
            last_rollup = now
            try:
                await asyncio.to_thread(_guarded, lock, _rollup_and_prune, conn, cfg)
            except Exception:
                log.exception("rollup/prune failed")
        await asyncio.sleep(cfg.poll_interval)


def _rollup_and_prune(conn, cfg: Config) -> None:
    db.rollup(conn)
    deleted = db.prune(conn, cfg.retention_days)
    conn.commit()
    if deleted:
        log.info("pruned %d raw snapshot rows", deleted)
