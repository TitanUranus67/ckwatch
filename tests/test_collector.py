from app import db
from app.collector import collect_once
from app.config import Config


def _cfg(log_dir, tmp_path):
    cfg = Config()
    cfg.log_dir = str(log_dir)
    cfg.db_path = str(tmp_path / "t.db")
    return cfg


def test_collect_once_populates(log_dir, tmp_path):
    cfg = _cfg(log_dir, tmp_path)
    conn = db.connect(cfg.db_path)
    assert collect_once(conn, cfg, now=1_800_000_000)

    pool = db.latest_pool(conn)
    assert pool is not None
    assert pool["hashrate1d"] == 8.84e12
    assert pool["accepted"] == 50619919063

    workers = {w["worker"]: w for w in db.latest_workers(conn)}
    assert set(workers) == {"bitaxe-gamma01", "bitaxe-gamma02", "Nerdqaxe", "MRR1"}
    g1 = workers["bitaxe-gamma01"]
    assert g1["hashrate1m"] == 135e6
    assert g1["bestever"] == 6432394954
    assert g1["lastshare"] == 1785896960
    # MRR1 has no workers connected -> all-zero current hashrates from summary
    assert workers["MRR1"]["hashrate1m"] == 0.0


def test_collect_once_missing_files(tmp_path):
    cfg = _cfg(tmp_path / "nonexistent", tmp_path)
    conn = db.connect(cfg.db_path)
    assert collect_once(conn, cfg) is False  # logs warnings, does not crash


def test_collect_once_skips_status_files(log_dir, tmp_path):
    # users/users.status must not be treated as a user file
    cfg = _cfg(log_dir, tmp_path)
    conn = db.connect(cfg.db_path)
    collect_once(conn, cfg)
    workers = [w["worker"] for w in db.latest_workers(conn)]
    assert "users.status" not in workers


def test_collect_once_after_user_appears(log_dir, tmp_path):
    cfg = _cfg(log_dir, tmp_path)
    conn = db.connect(cfg.db_path)
    collect_once(conn, cfg, now=1000)
    # a new user file shows up between polls
    (log_dir / "users" / "newminer").write_text(
        '{"hashrate1m": "1T", "shares": 5, "bestshare": 9, "bestever": 9,'
        ' "lastshare": 1060, "worker": [{"workername": "newminer",'
        ' "hashrate1m": "1T", "shares": 5, "bestshare": 9, "bestever": 9,'
        ' "lastshare": 1060}]}'
    )
    collect_once(conn, cfg, now=1060)
    workers = {w["worker"] for w in db.latest_workers(conn)}
    assert "newminer" in workers
