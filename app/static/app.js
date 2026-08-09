/* ckwatch dashboard — fetches /api/* every ~10s, renders cards, table, uPlot charts. */
"use strict";

const REFRESH_MS = 10000;
let currentRange = "24h";
let poolChart = null;
let workerCharts = {};

function fmtHashrate(hs) {
  if (hs == null) return "—";
  if (hs <= 0) return "0";
  const units = ["H/s", "kH/s", "MH/s", "GH/s", "TH/s", "PH/s", "EH/s"];
  const i = Math.min(Math.floor(Math.log10(hs) / 3), units.length - 1);
  const v = hs / Math.pow(10, 3 * i);
  return (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)) + " " + units[i];
}

function fmtInt(n) {
  if (n == null) return "—";
  return Math.round(n).toLocaleString("en-US");
}

function fmtDiff(d) {
  if (d == null) return "—";
  if (d >= 1e12) return (d / 1e12).toFixed(2) + "T";
  if (d >= 1e9) return (d / 1e9).toFixed(2) + "G";
  if (d >= 1e6) return (d / 1e6).toFixed(2) + "M";
  if (d >= 1e3) return (d / 1e3).toFixed(2) + "K";
  return Number(d).toFixed(2);
}

function fmtAge(s) {
  if (s == null) return "—";
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return (s / 3600).toFixed(1) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

function fmtUptime(s) {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return (d ? d + "d " : "") + (h ? h + "h " : "") + m + "m";
}

function showErr(msg) {
  const el = document.getElementById("err");
  el.style.display = msg ? "block" : "none";
  el.textContent = msg || "";
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

function card(label, value, sub) {
  return `<div class="card"><div class="label">${label}</div>` +
    `<div class="value">${value}</div>` +
    (sub ? `<div class="sub">${sub}</div>` : "") + `</div>`;
}

async function refreshPool() {
  const data = await getJSON("/api/pool");
  if (!data.pool) return;
  const p = data.pool, l = data.luck;
  document.getElementById("pool-cards").innerHTML = [
    card("Hashrate 1m", fmtHashrate(p.hashrate1m)),
    card("Hashrate 5m", fmtHashrate(p.hashrate5m)),
    card("Hashrate 1hr", fmtHashrate(p.hashrate1hr)),
    card("Hashrate 1d", fmtHashrate(p.hashrate1d)),
    card("Accepted", fmtInt(p.accepted)),
    card("Rejected", fmtInt(p.rejected)),
    card("SPS 1h", p.sps1h != null ? Number(p.sps1h).toFixed(4) : "—"),
    card("Best share", fmtDiff(p.bestshare)),
    card("Uptime", fmtUptime(p.runtime),
      `${p.users ?? 0} users / ${p.workers ?? 0} workers / ${p.idle ?? 0} idle`),
  ].join("");

  const pct = l.best_share_pct || 0;
  const barPct = Math.min(Math.log10(pct + 1) / 2 * 100, 100); // log-scaled bar
  document.getElementById("luck").innerHTML = [
    card("Network difficulty", fmtDiff(l.network_difficulty),
      "source: " + (l.difficulty_source || "?")),
    `<div class="card"><div class="label">Best share vs difficulty</div>
       <div class="value">${pct.toFixed(4)}%</div>
       <div class="bar"><div style="width:${barPct}%"></div></div>
       <div class="sub">${fmtDiff(l.bestshare)} of ${fmtDiff(l.network_difficulty)}</div></div>`,
    card("Est. avg time to block", l.eta_human || "unknown",
      "at current 1d hashrate " + fmtHashrate(p.hashrate1d)),
  ].join("");
}

async function refreshWorkers() {
  const data = await getJSON("/api/workers");
  const tb = document.querySelector("#workers tbody");
  tb.innerHTML = data.workers.map(w => `<tr>
    <td>${w.worker}</td>
    <td><span class="dot ${w.status}"></span>${w.status}</td>
    <td>${fmtHashrate(w.hashrate1m)}</td>
    <td>${fmtHashrate(w.hashrate5m)}</td>
    <td>${fmtHashrate(w.hashrate1hr)}</td>
    <td>${fmtHashrate(w.hashrate1d)}</td>
    <td>${fmtHashrate(w.hashrate7d)}</td>
    <td>${fmtInt(w.shares)}</td>
    <td>${fmtDiff(w.bestshare)}</td>
    <td>${fmtDiff(w.bestever)}</td>
    <td>${fmtAge(w.lastshare_age_s)}</td>
  </tr>`).join("");
}

function chartOpts(title, series) {
  return {
    title,
    width: document.getElementById("chart-pool").clientWidth || 900,
    height: 240,
    scales: { x: { time: true }, y: { auto: true } },
    axes: [
      { stroke: "#8b949e", grid: { stroke: "#21262d" } },
      {
        stroke: "#8b949e", grid: { stroke: "#21262d" },
        size: 70, // leave room for labels like "10.52 TH/s"
        values: (u, vals) => vals.map(v => fmtHashrate(v)),
      },
    ],
    series,
    cursor: { show: true },
    legend: { show: true },
  };
}

const COLORS = ["#f7931a", "#58a6ff", "#3fb950", "#f778ba", "#d29922", "#39c5cf", "#ff7b72"];

function renderCharts(data) {
  const xs = data.pool.map(r => r.ts);
  const ys = data.pool.map(r => r.hashrate1d);
  const opts = chartOpts("Pool hashrate (1d avg)", [
    {},
    { label: "pool 1d", stroke: COLORS[0], width: 2 },
  ]);
  if (poolChart) {
    poolChart.setData([xs, ys]);
  } else {
    poolChart = new uPlot(opts, [xs, ys], document.getElementById("chart-pool"));
  }

  const wc = document.getElementById("chart-workers");
  const names = Object.keys(data.workers).sort();
  // Per-worker 1d hashrate over the same time axis.
  const allTs = new Set();
  names.forEach(n => data.workers[n].forEach(r => allTs.add(r.ts)));
  const axis = [...allTs].sort((a, b) => a - b);
  const idx = new Map(axis.map((t, i) => [t, i]));
  const seriesData = names.map(n => {
    const arr = new Array(axis.length).fill(null);
    data.workers[n].forEach(r => { arr[idx.get(r.ts)] = r.hashrate1d; });
    return arr;
  });
  const wOpts = chartOpts("Worker hashrate (1d avg)", [
    {},
    ...names.map((n, i) => ({ label: n, stroke: COLORS[(i + 1) % COLORS.length], width: 1.5 })),
  ]);
  wOpts.height = 280;
  const chartData = [axis, ...seriesData];
  if (workerCharts.main) {
    workerCharts.main.setData(chartData);
  } else if (axis.length) {
    workerCharts.main = new uPlot(wOpts, chartData, wc);
  }
}

async function refreshHistory() {
  const data = await getJSON("/api/history?range=" + currentRange);
  renderCharts(data);
}

async function refreshBests() {
  const data = await getJSON("/api/bests?limit=10");
  const el = document.getElementById("bests");
  if (!data.bests.length) {
    el.innerHTML = '<div class="empty">No best-share events recorded yet.</div>';
    return;
  }
  el.innerHTML = "<table><thead><tr><th>Time</th><th>Worker</th><th>New best</th></tr></thead><tbody>" +
    data.bests.map(b => `<tr${b.pool_record ? ' class="pool-record"' : ""}>
      <td>${new Date(b.ts * 1000).toLocaleString()}</td>
      <td style="text-align:left">${b.worker}</td>
      <td>${fmtDiff(b.value)}${b.pool_record ? " &#9733; pool record" : ""}</td>
    </tr>`).join("") + "</tbody></table>";
}

async function refreshBlocks() {
  const data = await getJSON("/api/blocks");
  const el = document.getElementById("blocks");
  if (!data.blocks.length) {
    el.innerHTML = '<div class="empty">No blocks found yet. Good luck &#127808;</div>';
    return;
  }
  el.innerHTML = "<table><thead><tr><th>Time</th><th>Detail</th></tr></thead><tbody>" +
    data.blocks.map(b => `<tr><td>${new Date(b.ts * 1000).toLocaleString()}</td><td style="text-align:left">${b.text}</td></tr>`).join("") +
    "</tbody></table>";
}

async function refreshAll() {
  try {
    await Promise.all([refreshPool(), refreshWorkers(), refreshHistory(), refreshBlocks(), refreshBests()]);
    showErr(null);
    document.getElementById("updated").textContent =
      "updated " + new Date().toLocaleTimeString();
  } catch (e) {
    showErr("API error: " + e.message);
  }
}

document.getElementById("ranges").addEventListener("click", e => {
  if (e.target.tagName !== "BUTTON") return;
  currentRange = e.target.dataset.r;
  document.querySelectorAll("#ranges button").forEach(b => b.classList.toggle("sel", b === e.target));
  refreshHistory().catch(e => showErr("API error: " + e.message));
});

refreshAll();
setInterval(refreshAll, REFRESH_MS);
