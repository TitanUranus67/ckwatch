import time

from fastapi.testclient import TestClient

from app import db
from app.config import Config
from app.luck import DifficultyCache
from app.web import create_app


def _make_client(tmp_path, monkeypatch):
    # keep tests offline: stub out the network difficulty fetch
    monkeypatch.setattr(
        DifficultyCache, "get", lambda self: (self.fallback, "fallback")
    )
    cfg = Config()
    # Empty log dir: the background collector finds nothing, so seeded rows
    # below stay the latest (test_collector.py covers the collector itself).
    cfg.log_dir = str(tmp_path / "empty-logs")
    cfg.db_path = str(tmp_path / "data" / "web.db")
    cfg.poll_interval = 3600  # background loops idle; we drive data manually
    cfg.rollup_interval = 3600
    cfg.network_difficulty_fallback = 1.2e14
    app = create_app(cfg)
    return TestClient(app), cfg


def _seed(conn):
    now = int(time.time())
    for i in range(10):
        ts = now - (10 - i) * 60
        db.insert_pool_snapshot(conn, ts, {
            "runtime": 1000 + i, "Users": 1, "Workers": 2, "hashrate1m": "8T",
            "hashrate5m": "8T", "hashrate1d": "8T", "accepted": 100 + i,
            "rejected": 1, "bestshare": 6.7e10, "SPS1h": 0.01,
        })
        db.insert_user_snapshot(conn, ts, "bitaxe-gamma01", {
            "hashrate1m": "135M", "hashrate1d": "650G", "shares": 1000 + i,
            "bestshare": 6.4e9, "bestever": 6.4e9, "lastshare": now - 30,
        })
    db.insert_block(conn, now - 3600, None, "Solved and confirmed block 881234")
    conn.commit()


def test_api_endpoints(tmp_path, monkeypatch):
    client, cfg = _make_client(tmp_path, monkeypatch)
    with client:
        conn = db.connect(cfg.db_path)
        _seed(conn)

        r = client.get("/api/pool")
        assert r.status_code == 200
        body = r.json()
        assert body["pool"]["hashrate1m"] == 8e12
        assert body["luck"]["network_difficulty"] == 1.2e14
        assert body["luck"]["best_share_pct"] > 0
        assert body["luck"]["eta_seconds"] > 0
        assert body["luck"]["eta_human"].endswith("years")

        r = client.get("/api/workers")
        assert r.status_code == 200
        workers = r.json()["workers"]
        assert len(workers) == 1
        assert workers[0]["worker"] == "bitaxe-gamma01"
        assert workers[0]["status"] == "active"

        for rng in ("24h", "7d", "30d", "all"):
            r = client.get(f"/api/history?range={rng}")
            assert r.status_code == 200
            hist = r.json()
            assert len(hist["pool"]) >= 1
            assert "bitaxe-gamma01" in hist["workers"]

        r = client.get("/api/blocks")
        assert r.status_code == 200
        blocks = r.json()["blocks"]
        assert len(blocks) == 1
        assert "881234" in blocks[0]["text"]

        r = client.get("/api/bests")
        assert r.status_code == 200
        bests = r.json()["bests"]
        assert len(bests) == 1  # seeded constant bestever -> one baseline event
        assert bests[0]["worker"] == "bitaxe-gamma01"
        assert bests[0]["value"] == 6.4e9

        r = client.get("/")
        assert r.status_code == 200
        assert "ckwatch" in r.text

        r = client.get("/static/vendor/uPlot.iife.min.js")
        assert r.status_code == 200


def test_api_pool_no_data(tmp_path, monkeypatch):
    cfg = Config()
    cfg.log_dir = str(tmp_path / "empty-logs")
    cfg.db_path = str(tmp_path / "data2" / "web.db")
    cfg.poll_interval = 3600
    monkeypatch.setattr(
        DifficultyCache, "get", lambda self: (self.fallback, "fallback")
    )
    client = TestClient(create_app(cfg))
    with client:
        assert client.get("/api/pool").json() == {"status": "no data yet"}


def test_stale_worker_hashrates_masked(tmp_path, monkeypatch):
    """ckpool freezes a disconnected worker's decaying-average hashrates
    mid-decay; windows older than the last share must read as zero."""
    client, cfg = _make_client(tmp_path, monkeypatch)
    with client:
        conn = db.connect(cfg.db_path)
        now = int(time.time())
        db.insert_user_snapshot(conn, now, "MRR1", {
            "hashrate1m": "0", "hashrate5m": "0", "hashrate1hr": "0",
            "hashrate1d": "5M", "hashrate7d": "7.77T",
            "shares": 33738103991, "bestshare": 4.7e9, "bestever": 4.7e9,
            "lastshare": now - 115 * 86400,
        })
        conn.commit()

        r = client.get("/api/workers")
        assert r.status_code == 200
        w = r.json()["workers"][0]
        assert w["status"] == "offline"
        assert w["hashrate1d"] == 0.0
        assert w["hashrate7d"] == 0.0
