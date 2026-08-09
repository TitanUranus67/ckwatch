# ckwatch

A single-user web dashboard for a locally-hosted [ckpool](https://bitbucket.org/ckolivas/ckpool)
Bitcoin solo-mining pool — a lightweight ckstats replacement. Python + FastAPI +
SQLite in one small container; no MySQL, no node, no miner polling. All data is
read (read-only) from ckpool's own log directory.

## Features

- Pool summary cards: hashrates (1m/5m/1hr/1d), accepted/rejected shares, SPS, best share, uptime.
- Per-worker table: hashrates, shares, best difficulty (`bestshare`/`bestever`), last-share age, active/idle/offline status.
- Recent bests: the last 10 times any worker beat its previous best difficulty (backfilled from the log, pool record highlighted).
- Hashrate history charts (pool + per-worker) with 24h / 7d / 30d / all ranges.
- **First-run backfill**: parses months of history out of the periodic
  `User <name>:{json}` / `Pool:{json}` lines in `ckpool.log`, so charts are
  populated from day one.
- Solo-luck panel: best share vs current network difficulty (fetched from
  mempool.space, cached, with a static fallback) and estimated average time-to-block.
- Block-found detection (`Solved and confirmed block` log lines) with an
  plus new-personal-best and worker offline/recovered alerts via ntfy.sh (off by default).
- Retention: raw per-minute snapshots kept 30 days, hourly rollups kept forever.

## How it works

ckpool writes status into its log directory:

- `pool/pool.status` — 3 JSON lines (uptime/users, hashrates, diff/shares/bestshare/SPS)
- `users/<name>` — per-user JSON with a nested `worker` array
- `ckpool.log` — periodic machine-readable `User <name>:{json}` and `Pool:{json}`
  lines, plus `Solved and confirmed block ...` when a block is hit

ckwatch snapshots the status files every 60 s and tails `ckpool.log`
(offset-tracked, rotation/truncation-safe) for backfill and block events. It
never writes to the log directory — mount it read-only.

## Run with Docker (unraid)

1. Copy this repo to the server (e.g. `/mnt/user/appdata/ckwatch`).
2. Edit `docker-compose.yml` and point the log volume at your ckpool log dir:
   ```yaml
   - /mnt/user/appdata/ckpool/logs:/ckpool/logs:ro
   ```
3. Build and start:
   ```sh
   docker compose up -d --build
   ```
4. Open `http://<server>:8080`.

On unraid you can also add it via the Docker UI: "Add Container" → build from
the Dockerfile path, port `8080:8080`, and the two volume mappings above
(`/mnt/user/appdata/ckpool/logs` → `/ckpool/logs` read-only,
`/mnt/user/appdata/ckwatch/data` → `/data`).

Data (SQLite DB + log-tail offset state) persists in `./data`.

## Configuration

See `config.toml`. Everything can be overridden with environment variables:

| Env var          | Default             | Purpose                          |
|------------------|---------------------|----------------------------------|
| `CKWATCH_LOG_DIR`| `/ckpool/logs`      | ckpool log directory             |
| `CKWATCH_DB`     | `./data/ckwatch.db` | SQLite path                      |
| `CKWATCH_PORT`   | `8080`              | listen port                      |
| `CKWATCH_HOST`   | `0.0.0.0`           | listen address                   |
| `CKWATCH_CONFIG` | `./config.toml`     | config file location             |

The solo-luck panel fetches network difficulty from mempool.space (cached
10 min). If the host has no internet, set `network_difficulty_fallback` in
`config.toml` to the current difficulty after each retarget.

To get notifications (block found, new personal best, worker offline/recovered), enable ntfy in `config.toml`:

```toml
[ntfy]
enabled = true
url = "https://ntfy.sh/your-private-topic"
```

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # offline tests against real ckpool fixtures

# run locally against a ckpool log dir
CKWATCH_LOG_DIR=/path/to/ckpool/logs CKWATCH_DB=./data/ckwatch.db \
  .venv/bin/python -m app.web
```

## API

- `GET /api/pool` — latest pool snapshot + solo-luck panel data
- `GET /api/workers` — latest per-worker stats with status
- `GET /api/history?range=24h|7d|30d|all` — downsampled pool/worker hashrate series
- `GET /api/blocks` — blocks found
- `GET /api/bests?limit=10` — recent new-best events per worker
- `GET /` — the dashboard
