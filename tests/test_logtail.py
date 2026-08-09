import json
import shutil
from pathlib import Path

from app import db
from app.config import Config
from app.logtail import LogTailer

FIXTURE_LOG = Path(__file__).parent / "fixtures" / "ckpool-log-slice.log"


def _setup(tmp_path, copy_log=True):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    if copy_log:
        shutil.copy(FIXTURE_LOG, log_dir / "ckpool.log")
    cfg = Config()
    cfg.log_dir = str(log_dir)
    cfg.db_path = str(tmp_path / "data" / "t.db")
    (tmp_path / "data").mkdir()
    conn = db.connect(cfg.db_path)
    return cfg, conn


def test_backfill_first_run(tmp_path):
    cfg, conn = _setup(tmp_path)
    blocks_seen = []
    tailer = LogTailer(conn, cfg, on_block=lambda ev: blocks_seen.append(ev))
    n = tailer.scan_to_eof()
    assert n > 100

    pools = conn.execute("SELECT COUNT(*) c FROM pool_snapshots").fetchone()["c"]
    workers = conn.execute("SELECT COUNT(*) c FROM worker_snapshots").fetchone()["c"]
    assert pools > 10  # one row per Pool: minute (3 JSON docs merged)
    assert workers > 10

    # pool fragments merged into one row per minute
    row = conn.execute("SELECT * FROM pool_snapshots ORDER BY ts LIMIT 1").fetchone()
    assert row["runtime"] is not None
    assert row["hashrate1m"] is not None
    assert row["accepted"] is not None

    # block detected from the synthetic fixture line
    blocks = db.list_blocks(conn)
    assert len(blocks) == 1
    assert "881234" in blocks[0]["text"]
    assert len(blocks_seen) == 1

    # state file written, second run consumes nothing new
    state = json.loads((Path(cfg.state_file)).read_text())
    assert state["offset"] > 0
    assert tailer.scan_to_eof() == 0
    assert conn.execute("SELECT COUNT(*) c FROM pool_snapshots").fetchone()["c"] == pools


def test_resume_and_follow(tmp_path):
    cfg, conn = _setup(tmp_path)
    tailer = LogTailer(conn, cfg)
    tailer.scan_to_eof()
    before = conn.execute("SELECT COUNT(*) c FROM pool_snapshots").fetchone()["c"]

    # new lines appended after the offset
    with open(cfg.log_file, "a") as f:
        f.write('[2026-03-06 00:00:00.000] Pool:{"runtime": 999, "Users": 1}\n')
        f.write('[2026-03-06 00:00:00.000] Pool:{"hashrate1m": "10T"}\n')
        f.write('[2026-03-06 00:00:00.000] Pool:{"accepted": 5}\n')

    # a NEW tailer instance resumes from the state file, no double counting
    tailer2 = LogTailer(conn, cfg)
    n = tailer2.scan_to_eof()
    assert n == 3
    after = conn.execute("SELECT COUNT(*) c FROM pool_snapshots").fetchone()["c"]
    assert after == before + 1
    row = conn.execute(
        "SELECT * FROM pool_snapshots ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    assert row["runtime"] == 999
    assert row["hashrate1m"] == 10e12
    assert row["accepted"] == 5


def test_truncation_rescans_without_duplicates(tmp_path):
    cfg, conn = _setup(tmp_path)
    tailer = LogTailer(conn, cfg)
    tailer.scan_to_eof()
    before = conn.execute("SELECT COUNT(*) c FROM pool_snapshots").fetchone()["c"]

    # log rotated: new file with (overlapping) older content
    lines = FIXTURE_LOG.read_text().splitlines(keepends=True)
    with open(cfg.log_file, "w") as f:
        f.writelines(lines[:50])
    tailer2 = LogTailer(conn, cfg)
    tailer2.scan_to_eof()
    after = conn.execute("SELECT COUNT(*) c FROM pool_snapshots").fetchone()["c"]
    assert after == before  # INSERT OR IGNORE deduped the overlap


def test_missing_log_file(tmp_path):
    cfg, conn = _setup(tmp_path, copy_log=False)
    tailer = LogTailer(conn, cfg)
    assert tailer.scan_to_eof() == 0  # waits quietly for the log to appear

