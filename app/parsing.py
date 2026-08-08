"""Parsing helpers for ckpool status files and ckpool.log lines."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

# ckpool hashrate strings: "135M", "5.82T", "0", "912G" -> float H/s
_SUFFIXES = {
    "": 1.0,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
    "P": 1e15,
    "E": 1e18,
}
_HASHRATE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+(?:[eE][+-]?\d+)?)\s*([KMGTPE]?)\s*$")

# [2026-02-26 12:52:45.996] User bitaxe-gamma01:{...}
_LOG_PREFIX_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)?\]\s*(.*)$")
_LOG_USER_RE = re.compile(r"^User (\S+):(\{.*)$")
_LOG_POOL_RE = re.compile(r"^Pool:(\{.*)$")

BLOCK_PHRASE = "Solved and confirmed block"


def parse_hashrate(value) -> float:
    """Normalize a ckpool hashrate string/number to float hashes per second."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    m = _HASHRATE_RE.match(str(value))
    if not m:
        return 0.0
    return float(m.group(1)) * _SUFFIXES[m.group(2).upper()]


def parse_pool_status(text: str) -> dict:
    """Parse pool.status: three separate JSON lines merged into one dict."""
    merged: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if line:
            merged.update(json.loads(line))
    return merged


def parse_user_status(text: str) -> dict:
    """Parse a users/<name> file (pretty-printed JSON with a worker array)."""
    return json.loads(text)


def parse_log_timestamp(prefix: str) -> int:
    """Parse '[YYYY-MM-DD HH:MM:SS]' (already stripped of brackets/ms) to epoch seconds.

    ckpool logs in local time without a zone; we interpret it as UTC. Only
    relative ordering and bucketing matter for backfill, so this is fine.
    """
    dt = datetime.strptime(prefix, "%Y-%m-%d %H:%M:%S")
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def parse_log_line(line: str) -> dict | None:
    """Parse one ckpool.log line.

    Returns one of:
      {"kind": "user", "ts": int, "name": str, "data": dict}
      {"kind": "pool", "ts": int, "data": dict}
      {"kind": "block", "ts": int|None, "text": str}
      None for anything unrecognized.
    """
    if not line.startswith("["):
        # Block lines should always carry a timestamp prefix, but detect the
        # phrase even on oddly formatted lines.
        if BLOCK_PHRASE in line:
            return {"kind": "block", "ts": None, "text": line.strip()}
        return None
    m = _LOG_PREFIX_RE.match(line)
    if not m:
        return None
    ts = parse_log_timestamp(m.group(1))
    rest = m.group(2)
    if BLOCK_PHRASE in rest:
        return {"kind": "block", "ts": ts, "text": rest.strip()}
    um = _LOG_USER_RE.match(rest)
    if um:
        try:
            data = json.loads(um.group(2))
        except json.JSONDecodeError:
            return None
        return {"kind": "user", "ts": ts, "name": um.group(1), "data": data}
    pm = _LOG_POOL_RE.match(rest)
    if pm:
        try:
            data = json.loads(pm.group(1))
        except json.JSONDecodeError:
            return None
        return {"kind": "pool", "ts": ts, "data": data}
    return None
