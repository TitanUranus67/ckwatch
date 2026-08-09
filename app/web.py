"""FastAPI app: JSON API + static dashboard, background collector and log tailer."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .collector import collector_loop, worker_status
from .config import Config, load_config
from .logtail import LogTailer, logtail_loop
from .luck import DifficultyCache, format_eta, send_ntfy, solo_luck

log = logging.getLogger("ckwatch.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"

RANGES = {
    "24h": (86400, 300),
    "7d": (7 * 86400, 1800),
    "30d": (30 * 86400, 7200),
    "all": (None, 7200),
}

# ckpool freezes a disconnected worker's decaying-average hashrates mid-decay
# (they only update on new shares), so a long-dead worker can show a nonzero
# 7d hashrate. Mask any window older than the worker's last share to zero.
HASHRATE_WINDOWS_S = {
    "hashrate1m": 60,
    "hashrate5m": 300,
    "hashrate15m": 900,
    "hashrate1hr": 3600,
    "hashrate6hr": 6 * 3600,
    "hashrate1d": 86400,
    "hashrate7d": 7 * 86400,
}


class AppState:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        db_dir = Path(cfg.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        self.conn = db.connect(cfg.db_path)
        self.lock = threading.Lock()
        self.difficulty = DifficultyCache(
            cfg.network_difficulty_fallback, cfg.difficulty_cache_ttl
        )
        self.tailor = LogTailer(self.conn, cfg, on_block=self._on_block)

    def _notify(self, title: str, message: str) -> None:
        if not self.cfg.ntfy.enabled:
            return
        try:
            send_ntfy(self.cfg.ntfy.url, self.cfg.ntfy.priority, title, message)
        except Exception:
            log.exception("ntfy notification failed")

    def _on_block(self, ev: dict) -> None:
        self._notify("ckwatch: BLOCK FOUND!",
                     f"ckpool solved a block!\n{ev.get('text', '')}")

    def _on_best(self, worker: str, value: float) -> None:
        if not self.cfg.ntfy.notify_best:
            return
        log.info("new best for %s: %.0f", worker, value)
        self._notify("ckwatch: new best!",
                     f"{worker} set a new best difficulty: {_fmt_diff(value)}")

    def _on_status(self, worker: str, status: str) -> None:
        if not self.cfg.ntfy.notify_offline:
            return
        log.info("worker %s -> %s", worker, status)
        if status == "offline":
            self._notify("ckwatch: worker offline",
                         f"{worker} has not submitted a share in over 30 minutes")
        else:
            self._notify("ckwatch: worker back online",
                         f"{worker} is submitting shares again ({status})")


def _fmt_diff(d: float) -> str:
    for unit, scale in (("T", 1e12), ("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if d >= scale:
            return f"{d / scale:.2f}{unit}"
    return f"{d:.2f}"


def _worker_status(w: dict, now: int) -> str:
    return worker_status(w.get("lastshare"), now)


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    state = AppState(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = [
            asyncio.create_task(collector_loop(
                state.conn, cfg, state.lock,
                on_best=state._on_best, on_status=state._on_status)),
            asyncio.create_task(logtail_loop(state.tailor, lock=state.lock)),
        ]
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="ckwatch", lifespan=lifespan)

    @app.get("/api/pool")
    def api_pool():
        with state.lock:
            latest = db.latest_pool(state.conn)
        if latest is None:
            return {"status": "no data yet"}
        now = int(time.time())
        difficulty, diff_source = state.difficulty.get()
        luck = solo_luck(
            latest.get("bestshare") or 0.0,
            latest.get("hashrate1d") or 0.0,
            difficulty,
        )
        luck["difficulty_source"] = diff_source
        luck["eta_human"] = format_eta(luck["eta_seconds"])
        latest["snapshot_age_s"] = now - (latest.get("ts") or now)
        return {"pool": latest, "luck": luck}

    @app.get("/api/workers")
    def api_workers():
        now = int(time.time())
        with state.lock:
            workers = db.latest_workers(state.conn)
        for w in workers:
            w["status"] = _worker_status(w, now)
            age = now - (w.get("lastshare") or 0)
            w["lastshare_age_s"] = age
            for field, window in HASHRATE_WINDOWS_S.items():
                if field in w and age > window:
                    w[field] = 0.0
        return {"workers": workers}

    @app.get("/api/history")
    def api_history(range: str = Query("24h", pattern="^(24h|7d|30d|all)$")):
        span, bucket = RANGES[range]
        now = int(time.time())
        with state.lock:
            if span is None:
                raw_since = now - cfg.retention_days * 86400
                pool = db.pool_history_hourly(state.conn, raw_since)
                pool += db.pool_history(state.conn, raw_since, bucket)
                workers = db.worker_history_hourly(state.conn, raw_since)
                workers += db.worker_history(state.conn, raw_since, bucket)
            else:
                since = now - span
                pool = db.pool_history(state.conn, since, bucket)
                workers = db.worker_history(state.conn, since, bucket)
        by_worker: dict[str, list[dict]] = {}
        for row in workers:
            by_worker.setdefault(row.pop("worker"), []).append(row)
        return {"range": range, "bucket": bucket, "pool": pool, "workers": by_worker}

    @app.get("/api/blocks")
    def api_blocks():
        with state.lock:
            return {"blocks": db.list_blocks(state.conn)}

    @app.get("/api/bests")
    def api_bests(limit: int = Query(10, ge=1, le=100)):
        with state.lock:
            return {"bests": db.list_bests(state.conn, limit)}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def main() -> None:
    import uvicorn

    cfg = load_config()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
