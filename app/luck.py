"""Solo-luck helpers: network difficulty (mempool.space + fallback),
best-share percentage, chance-per-period, and estimated time-to-block."""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.request

log = logging.getLogger("ckwatch.luck")

_MEMPOOL_TIP_HASH = "https://mempool.space/api/blocks/tip/hash"
_MEMPOOL_BLOCK = "https://mempool.space/api/block/{}"
_TIMEOUT = 5.0

HASHES_PER_DIFF1 = 2**32  # expected hashes per share at difficulty 1


class DifficultyCache:
    """Fetch current network difficulty, cache it, fall back to a static value."""

    def __init__(self, fallback: float, ttl: int = 600) -> None:
        self.fallback = float(fallback)
        self.ttl = ttl
        self._value: float | None = None
        self._fetched_at = 0.0

    def _fetch(self) -> float:
        with urllib.request.urlopen(_MEMPOOL_TIP_HASH, timeout=_TIMEOUT) as r:
            tip = r.read().decode().strip()
        with urllib.request.urlopen(_MEMPOOL_BLOCK.format(tip), timeout=_TIMEOUT) as r:
            block = json.loads(r.read().decode())
        return float(block["difficulty"])

    def get(self) -> tuple[float, str]:
        """Returns (difficulty, source) where source is 'live', 'cache' or 'fallback'."""
        now = time.time()
        if self._value is not None and now - self._fetched_at < self.ttl:
            return self._value, "cache"
        try:
            self._value = self._fetch()
            self._fetched_at = now
            return self._value, "live"
        except Exception as e:
            log.warning("difficulty fetch failed (%s); using fallback", e)
            if self._value is not None:
                return self._value, "cache"
            return self.fallback, "fallback"


BLOCK_INTERVAL_S = 600  # targeted seconds between blocks


def solo_luck(bestshare: float, hashrate1d: float, difficulty: float,
              accepted: float | None = None) -> dict:
    """Best share as % of network difficulty, ETA, chance per period,
    lifetime effort, and share of the global network."""
    pct = (bestshare / difficulty * 100.0) if difficulty > 0 else 0.0
    # Expected hashes per block = difficulty * 2^32; eta at current hashrate.
    eta = (difficulty * HASHES_PER_DIFF1 / hashrate1d) if hashrate1d > 0 else None
    out = {
        "bestshare": bestshare,
        "network_difficulty": difficulty,
        "best_share_pct": pct,
        "eta_seconds": eta,
    }
    if hashrate1d > 0 and difficulty > 0:
        work_per_block = difficulty * HASHES_PER_DIFF1

        def chance(seconds: float) -> float:
            # P(at least one block) = 1 - e^(-expected blocks)
            return 1.0 - math.exp(-hashrate1d * seconds / work_per_block)

        out["chance_day"] = chance(86400)
        out["chance_week"] = chance(7 * 86400)
        out["chance_year"] = chance(365 * 86400)
        # Global hashrate implied by difficulty at the 10-minute target.
        out["network_hashrate"] = work_per_block / BLOCK_INTERVAL_S
        out["network_share_pct"] = hashrate1d / out["network_hashrate"] * 100.0
    if accepted is not None and difficulty > 0:
        # Cumulative accepted difficulty vs one block's expected work.
        out["effort_pct"] = accepted / difficulty * 100.0
    return out


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    days = seconds / 86400.0
    if days < 1:
        return f"{seconds / 3600:.1f} hours"
    if days < 365:
        return f"{days:.1f} days"
    return f"{days / 365:.1f} years"


def send_ntfy(url: str, priority: str, title: str, message: str) -> None:
    req = urllib.request.Request(
        url,
        data=message.encode(),
        headers={"Title": title, "Priority": priority},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT):
        pass
