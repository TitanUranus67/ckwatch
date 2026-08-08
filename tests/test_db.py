from app import db


def _pool_data(hr="1T", accepted=100, bestshare=5000):
    return {
        "runtime": 60, "Users": 1, "Workers": 1, "Idle": 0, "Disconnected": 0,
        "lastupdate": 1, "hashrate1m": hr, "hashrate5m": hr, "hashrate15m": hr,
        "hashrate1hr": hr, "hashrate6hr": hr, "hashrate1d": hr, "hashrate7d": hr,
        "diff": 0.01, "accepted": accepted, "rejected": 1,
        "bestshare": bestshare, "SPS1m": 0.1, "SPS5m": 0.1, "SPS15m": 0.1, "SPS1h": 0.1,
    }


def test_insert_and_latest(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.insert_pool_snapshot(conn, 1000, _pool_data())
    db.insert_user_snapshot(conn, 1000, "alice", {
        "hashrate1m": "500G", "shares": 42, "bestshare": 123.5, "bestever": 123,
        "lastshare": 999,
    })
    db.insert_user_snapshot(conn, 1000, "rig1", {
        "hashrate1m": "1T", "shares": 10, "bestshare": 5, "bestever": 5, "lastshare": 998,
        "worker": [{"workername": "rig1.w0", "hashrate1m": "600G", "shares": 6,
                    "bestshare": 5, "bestever": 5, "lastshare": 998},
                   {"workername": "rig1.w1", "hashrate1m": "400G", "shares": 4,
                    "bestshare": 3, "bestever": 3, "lastshare": 997}],
    })
    conn.commit()

    latest = db.latest_pool(conn)
    assert latest["ts"] == 1000
    assert latest["hashrate1m"] == 1e12

    workers = {w["worker"]: w for w in db.latest_workers(conn)}
    assert workers["alice"]["hashrate1m"] == 500e9
    assert set(workers) == {"alice", "rig1.w0", "rig1.w1"}


def test_dedupe_same_ts(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.insert_pool_snapshot(conn, 1000, _pool_data())
    db.insert_pool_snapshot(conn, 1000, _pool_data())
    db.insert_user_snapshot(conn, 1000, "alice", {"hashrate1m": "1G", "shares": 1,
                                                  "lastshare": 1})
    db.insert_user_snapshot(conn, 1000, "alice", {"hashrate1m": "1G", "shares": 1,
                                                  "lastshare": 1})
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM pool_snapshots").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM worker_snapshots").fetchone()["c"] == 1


def test_rollup_and_prune(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    hour = 3600
    # 3 snapshots inside hour 0, 1 inside hour 1
    for ts in (0, 60, 120):
        db.insert_pool_snapshot(conn, ts, _pool_data(hr="1T", accepted=100 + ts))
        db.insert_user_snapshot(conn, ts, "alice",
                                {"hashrate1m": "2T", "shares": ts, "lastshare": ts})
    db.insert_pool_snapshot(conn, hour + 60, _pool_data(hr="3T"))
    conn.commit()

    db.rollup(conn, now=hour + 120)  # only hour 0 is fully elapsed
    conn.commit()
    rows = conn.execute("SELECT * FROM pool_hourly").fetchall()
    assert len(rows) == 1
    assert rows[0]["hour"] == 0
    assert rows[0]["hashrate1m"] == 1e12
    assert rows[0]["accepted"] == 220  # MAX of cumulative counter

    wh = conn.execute("SELECT * FROM worker_hourly").fetchall()
    assert len(wh) == 1 and wh[0]["worker"] == "alice" and wh[0]["shares"] == 120

    deleted = db.prune(conn, retention_days=1, now=4 * 86400)
    conn.commit()
    assert deleted > 0
    assert conn.execute("SELECT COUNT(*) c FROM pool_snapshots").fetchone()["c"] == 0
    # hourly rollups survive pruning
    assert conn.execute("SELECT COUNT(*) c FROM pool_hourly").fetchone()["c"] == 1


def test_history_bucketing(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    for ts in range(0, 3600, 60):
        db.insert_pool_snapshot(conn, ts, _pool_data(hr="1T"))
    conn.commit()
    hist = db.pool_history(conn, since=0, bucket=300)
    assert len(hist) == 12
    assert hist[0]["ts"] == 0
    assert hist[0]["hashrate1m"] == 1e12


def test_blocks(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.insert_block(conn, 1000, "alice", "Solved and confirmed block 1")
    conn.commit()
    blocks = db.list_blocks(conn)
    assert len(blocks) == 1
    assert blocks[0]["notified"] == 0
    db.mark_block_notified(conn, blocks[0]["id"])
    conn.commit()
    assert db.list_blocks(conn)[0]["notified"] == 1


def test_best_events(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    # first sighting establishes the baseline
    db.insert_user_snapshot(conn, 1000, "gamma01", {
        "hashrate1m": "600G", "shares": 100, "bestshare": 1.0e9,
        "bestever": 2.0e9, "lastshare": 1000,
    })
    # lower value: no event
    db.insert_user_snapshot(conn, 1060, "gamma01", {
        "hashrate1m": "600G", "shares": 200, "bestshare": 5.0e8,
        "bestever": 2.0e9, "lastshare": 1060,
    })
    # new best: event
    db.insert_user_snapshot(conn, 1120, "gamma01", {
        "hashrate1m": "600G", "shares": 300, "bestshare": 6.7e10,
        "bestever": 6.7e10, "lastshare": 1120,
    })
    # equal value: no duplicate event
    db.insert_user_snapshot(conn, 1180, "gamma01", {
        "hashrate1m": "600G", "shares": 400, "bestshare": 6.7e10,
        "bestever": 6.7e10, "lastshare": 1180,
    })
    # a second worker's lower best is not a pool record
    db.insert_user_snapshot(conn, 1240, "nerdqaxe", {
        "hashrate1m": "6T", "shares": 10, "bestshare": 6.0e9,
        "bestever": 6.0e9, "lastshare": 1240,
    })
    conn.commit()
    bests = db.list_bests(conn)
    assert [(b["worker"], b["value"]) for b in bests] == [
        ("nerdqaxe", 6.0e9),
        ("gamma01", 6.7e10),
        ("gamma01", 2.0e9),
    ]
    assert [b["pool_record"] for b in bests] == [0, 1, 0]
    assert bests[1]["ts"] == 1120


def test_best_events_from_worker_array(tmp_path):
    """Status files carry a worker array; each worker is tracked separately."""
    conn = db.connect(str(tmp_path / "t.db"))
    db.insert_user_snapshot(conn, 1000, "alice", {
        "hashrate1m": "1T", "shares": 1, "bestshare": 3.0e9, "bestever": 3.0e9,
        "lastshare": 1000,
        "worker": [
            {"workername": "alice.bitaxe", "bestshare": 1.0e9, "bestever": 1.0e9},
            {"workername": "alice.nerd", "bestshare": 3.0e9, "bestever": 3.0e9},
        ],
    })
    conn.commit()
    bests = db.list_bests(conn)
    assert {(b["worker"], b["value"]) for b in bests} == {
        ("alice.bitaxe", 1.0e9), ("alice.nerd", 3.0e9),
    }
