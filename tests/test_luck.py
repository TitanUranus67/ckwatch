from app.luck import DifficultyCache, format_eta, solo_luck


def test_solo_luck_math():
    luck = solo_luck(bestshare=6e9, hashrate1d=1e12, difficulty=1.2e14)
    assert luck["best_share_pct"] == 6e9 / 1.2e14 * 100
    # eta = diff * 2^32 / hashrate
    assert luck["eta_seconds"] == 1.2e14 * 2**32 / 1e12


def test_solo_luck_zero_hashrate():
    luck = solo_luck(bestshare=1, hashrate1d=0, difficulty=1.2e14)
    assert luck["eta_seconds"] is None


def test_format_eta():
    assert format_eta(None) == "unknown"
    assert format_eta(3600) == "1.0 hours"
    assert format_eta(86400 * 3) == "3.0 days"
    assert format_eta(86400 * 800).endswith("years")


def test_difficulty_cache_fallback(monkeypatch):
    def _boom(self):
        raise RuntimeError("offline")

    monkeypatch.setattr(DifficultyCache, "_fetch", _boom)
    cache = DifficultyCache(fallback=1.2e14, ttl=600)
    value, source = cache.get()
    assert value == 1.2e14
    assert source == "fallback"
