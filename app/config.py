"""Configuration: config.toml with environment variable overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"

# Fallback network difficulty (diff1 units), used when mempool.space is
# unreachable. Roughly the 2026-era difficulty; override in config.toml.
DEFAULT_NETWORK_DIFFICULTY = 1.2e14


@dataclass
class NtfyConfig:
    enabled: bool = False
    url: str = "https://ntfy.sh/ckwatch"
    priority: str = "high"


@dataclass
class Config:
    log_dir: str = "/ckpool/logs"
    db_path: str = "./data/ckwatch.db"
    host: str = "0.0.0.0"
    port: int = 8080
    poll_interval: int = 60          # seconds between status-file snapshots
    rollup_interval: int = 3600      # seconds between rollup/prune passes
    retention_days: int = 30         # raw 60s snapshots kept this long
    network_difficulty_fallback: float = DEFAULT_NETWORK_DIFFICULTY
    difficulty_cache_ttl: int = 600  # seconds
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)

    @property
    def log_file(self) -> str:
        return os.path.join(self.log_dir, "ckpool.log")

    @property
    def state_file(self) -> str:
        return os.path.join(os.path.dirname(self.db_path) or ".", "logtail.state")


def load_config(path: str | None = None) -> Config:
    cfg = Config()
    cfg_path = path or os.environ.get("CKWATCH_CONFIG") or str(DEFAULT_CONFIG)
    if os.path.exists(cfg_path):
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        for key in (
            "log_dir", "db_path", "host", "port", "poll_interval",
            "rollup_interval", "retention_days",
            "network_difficulty_fallback", "difficulty_cache_ttl",
        ):
            if key in data:
                setattr(cfg, key, data[key])
        if "ntfy" in data:
            for key in ("enabled", "url", "priority"):
                if key in data["ntfy"]:
                    setattr(cfg.ntfy, key, data["ntfy"][key])

    # Environment overrides (highest priority)
    if v := os.environ.get("CKWATCH_LOG_DIR"):
        cfg.log_dir = v
    if v := os.environ.get("CKWATCH_DB"):
        cfg.db_path = v
    if v := os.environ.get("CKWATCH_PORT"):
        cfg.port = int(v)
    if v := os.environ.get("CKWATCH_HOST"):
        cfg.host = v
    return cfg
