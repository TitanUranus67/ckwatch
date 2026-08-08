"""Log tailer: follow ckpool.log, backfill history on first run, detect blocks.

State (byte offset + inode) is kept in a small JSON state file next to the DB
so restarts resume where we left off. Rotation/truncation is detected via
inode change or the file shrinking below the saved offset.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable

from . import db
from .config import Config
from .parsing import parse_log_line

log = logging.getLogger("ckwatch.logtail")

_COMMIT_EVERY = 5000  # lines between commits during backfill


class LogTailer:
    def __init__(
        self,
        conn,
        cfg: Config,
        on_block: Callable[[dict], None] | None = None,
    ) -> None:
        self.conn = conn
        self.cfg = cfg
        self.on_block = on_block
        self.offset = 0
        self.inode: int | None = None
        # Pool lines arrive as 3 separate JSON docs per minute; merge per ts.
        self._pool_ts: int | None = None
        self._pool_frag: dict = {}
        self._pending = 0
        self._load_state()

    # -- state file -------------------------------------------------------

    def _load_state(self) -> None:
        try:
            with open(self.cfg.state_file, encoding="utf-8") as f:
                state = json.load(f)
            self.offset = int(state.get("offset", 0))
            self.inode = state.get("inode")
        except (FileNotFoundError, ValueError, OSError):
            self.offset = 0
            self.inode = None

    def _save_state(self) -> None:
        tmp = self.cfg.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"offset": self.offset, "inode": self.inode}, f)
        os.replace(tmp, self.cfg.state_file)

    # -- line handling ----------------------------------------------------

    def _flush_pool_frag(self) -> None:
        if self._pool_ts is not None and self._pool_frag:
            db.insert_pool_snapshot(self.conn, self._pool_ts, self._pool_frag)
            self._pending += 1
        self._pool_ts = None
        self._pool_frag = {}

    def handle_line(self, line: str) -> None:
        ev = parse_log_line(line)
        if ev is None:
            return
        if ev["kind"] == "pool":
            if self._pool_ts is not None and ev["ts"] != self._pool_ts:
                self._flush_pool_frag()
            self._pool_ts = ev["ts"]
            self._pool_frag.update(ev["data"])
        elif ev["kind"] == "user":
            self._flush_pool_frag()
            db.insert_user_snapshot(self.conn, ev["ts"], ev["name"], ev["data"])
            self._pending += 1
        elif ev["kind"] == "block":
            self._flush_pool_frag()
            log.warning("BLOCK FOUND: %s", ev["text"])
            db.insert_block(self.conn, ev.get("ts"), None, ev["text"])
            self.conn.commit()
            if self.on_block:
                try:
                    self.on_block(ev)
                except Exception:
                    log.exception("block notification failed")
        if self._pending >= _COMMIT_EVERY:
            self.conn.commit()
            self._pending = 0

    # -- file following ---------------------------------------------------

    def _open_at_offset(self):
        """Open the log, handling first run, rotation and truncation."""
        path = self.cfg.log_file
        st = os.stat(path)
        first_run = self.inode is None
        rotated = self.inode is not None and st.st_ino != self.inode
        truncated = st.st_size < self.offset
        if rotated or truncated:
            log.info("log %s detected; rescanning from start",
                     "rotation" if rotated else "truncation")
            self.offset = 0
        if first_run:
            log.info("first run: backfilling history from %s (%d bytes)",
                     path, st.st_size)
        f = open(path, encoding="utf-8", errors="replace")
        f.seek(self.offset)
        self.inode = st.st_ino
        return f

    def scan_to_eof(self) -> int:
        """Read all complete lines up to EOF. Returns number of lines read."""
        n = 0
        try:
            f = self._open_at_offset()
        except FileNotFoundError:
            log.debug("log file %s not found yet", self.cfg.log_file)
            return 0
        with f:
            while True:
                line = f.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    # Partial line (writer mid-write); re-read next pass.
                    f.seek(f.tell() - len(line.encode("utf-8", "replace")))
                    break
                self.handle_line(line)
                n += 1
            self._flush_pool_frag()
            self.offset = f.tell()
        self.conn.commit()
        self._pending = 0
        self._save_state()
        return n


async def logtail_loop(tailer: LogTailer, interval: float = 5.0, lock=None) -> None:
    log.info("log tailer started (%s)", tailer.cfg.log_file)
    while True:
        try:
            if lock is None:
                n = await asyncio.to_thread(tailer.scan_to_eof)
            else:
                def _scan() -> int:
                    with lock:
                        return tailer.scan_to_eof()

                n = await asyncio.to_thread(_scan)
            if n:
                log.debug("consumed %d log lines", n)
        except Exception:
            log.exception("log tailer iteration failed")
        await asyncio.sleep(interval)
