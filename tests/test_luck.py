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


def test_solo_luck_extended():
    import math

    diff = 1.2e14
    luck = solo_luck(bestshare=6e9, hashrate1d=1e12, difficulty=diff,
                     accepted=6e10)
    work_per_block = diff * 2**32
    assert math.isclose(luck["chance_day"],
                        1 - math.exp(-1e12 * 86400 / work_per_block))
    assert math.isclose(luck["chance_week"],
                        1 - math.exp(-1e12 * 7 * 86400 / work_per_block))
    assert math.isclose(luck["chance_year"],
                        1 - math.exp(-1e12 * 365 * 86400 / work_per_block))
    assert luck["network_hashrate"] == work_per_block / 600
    assert math.isclose(luck["network_share_pct"],
                        1e12 / luck["network_hashrate"] * 100)
    assert math.isclose(luck["effort_pct"], 6e10 / diff * 100)


def test_solo_luck_extended_absent_without_data():
    luck = solo_luck(bestshare=1, hashrate1d=0, difficulty=1.2e14)
    assert "chance_day" not in luck
    assert "effort_pct" not in luck  # no accepted passed
