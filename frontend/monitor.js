// NearStop Monitoring page (#monitor): each worker's strap environment sensors.
//
//   BMP180 -> air temperature (°C) + air pressure (hPa)
//   MQ-2   -> combustible gas, as the sensor's output voltage (mV)
//
// Live values come with every WebSocket update (state.site.workers[].env,
// graded by backend/safety.py). The 10-minute charts are fetched from
// GET /api/workers/{id}/environment every few seconds while this page is open.
// Uses helpers from app.js ($, esc, state, sitePage) and site.js (nsIcon, nsSetHTML, nsAgo).

"use strict";

const NS_ENV_WORDS = {
  gas: { ok: "Normal", warn: "Gas detected", danger: "High gas", warming: "Warming up", offline: "No reading",
    fault: "Sensor fault – check MQ-2 wiring (VCC 5 V, GND, AO)" },
  heat: { ok: "Normal", warn: "High heat", danger: "Dangerous heat", offline: "No reading" },
};
const NS_ENV_LVL = { ok: "ok", warn: "warn", danger: "danger", warming: "info", offline: "offline", fault: "warn" };
const NS_ENV_WINDOW_S = 600;          // charts show the last 10 minutes
const NS_ENV_FETCH_MS = 4000;

const nsEnvHistory = {};              // worker id -> [{t, temp_c, pressure_hpa, gas_mv, gas_ratio}]
let nsEnvFetchedAt = 0;
let nsEnvFetching = false;

// ---------------------------------------------------------------------------
// Render (called from siteRender ~5x per second)
// ---------------------------------------------------------------------------
function nsRenderMonitor(site) {
  // Nav dot: visible on every page while any worker's gas or heat is high
  const worst = site.workers.reduce((lvl, w) => {
    const e = w.env;
    if (!e) return lvl;
    if (e.gas_level === "danger" || e.heat_level === "danger") return "danger";
    if ((e.gas_level === "warn" || e.heat_level === "warn") && lvl !== "danger") return "warn";
    return lvl;
  }, null);
  const dot = $("ns-nav-alert");
  dot.hidden = !worst;
  dot.className = `ns-dot lvl-${worst || "ok"}`;

  if (sitePage !== "monitor") return;
  const box = $("ns-env");
  if (!site.workers.length) {
    nsSetHTML(box, '<div class="ns-empty">No workers set up</div>');
    return;
  }
  for (const w of site.workers) {
    let card = box.querySelector(`[data-key="${CSS.escape(w.id)}"]`);
    if (!card) {
      box.querySelector(".ns-empty")?.remove();
      card = document.createElement("section");
      card.dataset.key = w.id;
      card.className = "ns-glass ns-panel ns-env-worker";
      card.innerHTML = `<div class="ns-env-head"></div><div class="ns-env-tiles"></div>
        <div class="ns-env-charts">
          <figure class="ns-chart" data-key="temp_c"></figure>
          <figure class="ns-chart" data-key="pressure_hpa"></figure>
          <figure class="ns-chart" data-key="gas_mv"></figure>
        </div>
        <details class="ns-env-table"><summary>Show readings as a table</summary><div></div></details>`;
      box.appendChild(card);
    }
    nsSetHTML(card.querySelector(".ns-env-head"), nsEnvHead(w));
    nsSetHTML(card.querySelector(".ns-env-tiles"), nsEnvTiles(w));
  }
  if (Date.now() - nsEnvFetchedAt > NS_ENV_FETCH_MS) nsFetchEnvHistory(site);
}

function nsEnvHead(w) {
  const e = w.env;
  let sub;
  if (!e) sub = w.online ? "Strap online, no environment sensors reporting" : "Strap not connected";
  else if (e.stale) sub = `No new reading · last ${nsAgo(e.age_s)}`;
  else sub = `Strap sensors · updated ${e.age_s < 3 ? "just now" : `${Math.round(e.age_s)} s ago`}`;
  const lvl = e ? NS_ENV_LVL[e.level] : "offline";
  const word = !e ? "No data" : { ok: "All normal", warn: "Caution", danger: "Danger" }[e.level] || "No reading";
  return `
    <div class="ns-row">
      <div class="ns-mini-icon lvl-${lvl}">${nsIcon("i-worker")}</div>
      <div class="ns-item-main">
        <div class="ns-item-name">${esc(w.name)}</div>
        <div class="ns-item-line">${esc(sub)}</div>
      </div>
      <span class="ns-state lvl-${lvl}">${word}</span>
    </div>`;
}

function nsFmt(v, digits) {
  return v === null || v === undefined ? "–" : Number(v).toFixed(digits);
}

function nsEnvTiles(w) {
  const e = w.env;
  if (!e) {
    return `<div class="ns-empty ns-env-empty">No temperature, pressure or gas readings from ${esc(w.name)}'s strap yet.
      Wire the BMP180 and MQ-2 and flash <code>firmware/worker_strap</code> (see README, "Environment sensors").</div>`;
  }
  const th = e.thresholds;
  const heatLvl = NS_ENV_LVL[e.heat_level];
  const gasLvl = NS_ENV_LVL[e.gas_level];
  const dim = e.stale ? " ns-env-stale" : "";
  return `
    <div class="ns-env-tile lvl-${heatLvl}${dim}">
      <div class="ns-env-label">${nsIcon("i-heat")}Temperature</div>
      <div class="ns-env-value">${nsFmt(e.temp_c, 1)}<span>°C</span></div>
      <div class="ns-env-sub">Air temperature at the worker</div>
      <div class="ns-env-level"><span class="ns-dot"></span>${NS_ENV_WORDS.heat[e.heat_level]}</div>
      <div class="ns-env-th">High at ${th.heat_warn_c} °C · danger at ${th.heat_danger_c} °C</div>
    </div>
    <div class="ns-env-tile lvl-info${dim}">
      <div class="ns-env-label">${nsIcon("i-gauge")}Air pressure</div>
      <div class="ns-env-value">${nsFmt(e.pressure_hpa, 1)}<span>hPa</span></div>
      <div class="ns-env-sub">Barometric pressure at the worker</div>
      <div class="ns-env-level ns-env-level-plain">Monitoring only (sea level ≈ 1013 hPa)</div>
    </div>
    ${e.gas_source === "strap" ? `
    <div class="ns-env-tile lvl-${gasLvl}${dim}">
      <div class="ns-env-label">${nsIcon("i-flame")}Combustible gas</div>
      <div class="ns-env-value">${e.gas_ratio === null ? "–" : Math.round(e.gas_ratio * 100)}<span>% of clean air</span></div>
      <div class="ns-env-sub">MQ-2 sensor resistance (gas makes it drop) · ${nsFmt(e.gas_mv, 0)} mV</div>
      <div class="ns-env-level"><span class="ns-dot"></span>${e.gas_level === "warming" ? "Warming up / learning clean air" : NS_ENV_WORDS.gas[e.gas_level]}</div>
      <div class="ns-env-th">Detected at ≤ ${Math.round(th.gas_yellow_ratio * 100)}% · high at ≤ ${Math.round(th.gas_red_ratio * 100)}% (not ppm)</div>
    </div>` : `
    <div class="ns-env-tile lvl-${gasLvl}${dim}">
      <div class="ns-env-label">${nsIcon("i-flame")}Combustible gas</div>
      <div class="ns-env-value">${nsFmt(e.gas_mv, 0)}<span>mV</span></div>
      <div class="ns-env-sub">MQ-2 sensor output</div>
      <div class="ns-env-level"><span class="ns-dot"></span>${NS_ENV_WORDS.gas[e.gas_level]}</div>
      <div class="ns-env-th">Detected at ${th.gas_warn_mv} mV · high at ${th.gas_danger_mv} mV</div>
    </div>`}`;
}

// ---------------------------------------------------------------------------
// History + charts
// ---------------------------------------------------------------------------
async function nsFetchEnvHistory(site) {
  if (nsEnvFetching) return;
  nsEnvFetching = true;
  nsEnvFetchedAt = Date.now();
  try {
    for (const w of site.workers) {
      const resp = await fetch(`/api/workers/${encodeURIComponent(w.id)}/environment`, { cache: "no-store" });
      if (!resp.ok) continue;
      const data = await resp.json();
      nsEnvHistory[w.id] = { points: data.history, thresholds: data.thresholds };
    }
    nsDrawEnvCharts();
  } catch (e) {
    /* backend unreachable: the reconnect banner already says so */
  } finally {
    nsEnvFetching = false;
  }
}

const NS_CHARTS = {
  temp_c: { title: "Temperature", unit: "°C", digits: 1, minSpan: 4 },
  pressure_hpa: { title: "Air pressure", unit: "hPa", digits: 1, minSpan: 2 },
  gas_mv: { title: "Combustible gas", unit: "mV", digits: 0, minSpan: 200, lo: 0 },
  gas_pct: { title: "Gas sensor", unit: "% of clean air", digits: 0, minSpan: 20, lo: 0 },
};

function nsDrawEnvCharts() {
  if (sitePage !== "monitor") return;
  const now = state ? state.server_time : Date.now() / 1000;
  for (const card of document.querySelectorAll(".ns-env-worker")) {
    const hist = nsEnvHistory[card.dataset.key];
    const points = hist ? hist.points : [];
    for (const fig of card.querySelectorAll(".ns-chart")) {
      nsDrawChart(fig, points, fig.dataset.key, now, hist ? hist.thresholds : null);
    }
    nsEnvTable(card.querySelector(".ns-env-table div"), points);
  }
}

function nsDrawChart(fig, points, key, now, thresholds) {
  let pts = points.filter((p) => p.t >= now - NS_ENV_WINDOW_S);
  // Strap MQ-2 grades gas as resistance vs clean air: chart that (in %) when the strap sends it
  if (key === "gas_mv" && pts.some((p) => p.gas_ratio !== null && p.gas_ratio !== undefined)) {
    key = "gas_pct";
    pts = pts.map((p) => ({ ...p, gas_pct: p.gas_ratio === null || p.gas_ratio === undefined ? null : p.gas_ratio * 100 }));
  }
  const spec = NS_CHARTS[key];
  const vals = pts.map((p) => p[key]).filter((v) => v !== null && v !== undefined);
  const latest = vals.length ? vals[vals.length - 1] : null;
  const W = Math.max(260, Math.round(fig.clientWidth || 320)), H = 150;
  const m = { l: 44, r: 10, t: 10, b: 22 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;

  // Threshold lines on the gas chart only
  let lines = [];
  if (key === "gas_mv" && thresholds) {
    lines = [{ v: thresholds.gas_warn_mv, cls: "warn", label: "Detected" }, { v: thresholds.gas_danger_mv, cls: "danger", label: "High" }];
  } else if (key === "gas_pct" && thresholds) {   // gas makes the % DROP: lines below clean air
    lines = [{ v: thresholds.gas_yellow_ratio * 100, cls: "warn", label: "Detected" },
      { v: thresholds.gas_red_ratio * 100, cls: "danger", label: "High" }];
  }

  let lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 1;
  if (key === "gas_mv" && thresholds) hi = Math.max(hi, thresholds.gas_warn_mv * 1.08);
  if (key === "gas_pct" && thresholds) { lo = Math.min(lo, thresholds.gas_yellow_ratio * 100 * 0.9); hi = Math.max(hi, 110); }
  if (hi - lo < spec.minSpan) { const mid = (hi + lo) / 2; lo = mid - spec.minSpan / 2; hi = mid + spec.minSpan / 2; }
  const pad = (hi - lo) * 0.1;
  lo -= pad; hi += pad;
  if (spec.lo !== undefined) lo = Math.max(lo, spec.lo);
  if (spec.hi !== undefined) hi = Math.min(hi, spec.hi);

  const x = (t) => m.l + ((t - (now - NS_ENV_WINDOW_S)) / NS_ENV_WINDOW_S) * iw;
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * ih;

  // Line with gaps where readings are missing (null, or > 6 s between points)
  let d = "", prevT = null;
  for (const p of pts) {
    const v = p[key];
    if (v === null || v === undefined) { prevT = null; continue; }
    d += `${prevT === null || p.t - prevT > 6 ? "M" : "L"}${x(p.t).toFixed(1)},${y(v).toFixed(1)}`;
    prevT = p.t;
  }

  const ticks = [0, 1, 2].map((i) => lo + ((hi - lo) * (i + 0.5)) / 3);
  const grid = ticks.map((v) => `<line class="ns-grid" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`
    + `<text class="ns-axis" x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(spec.digits > 0 && hi - lo < 10 ? 1 : 0)}</text>`).join("");
  const xlab = [[0, "-10 min"], [0.5, "-5 min"], [1, "now"]].map(([f, t]) =>
    `<text class="ns-axis" x="${m.l + f * iw}" y="${H - 6}" text-anchor="${f === 0 ? "start" : f === 1 ? "end" : "middle"}">${t}</text>`).join("");
  const thr = lines.filter((l) => l.v > lo && l.v < hi).map((l) =>
    `<line class="ns-thr ns-thr-${l.cls}" x1="${m.l}" x2="${W - m.r}" y1="${y(l.v)}" y2="${y(l.v)}"/>`
    + `<text class="ns-axis ns-thr-label" x="${W - m.r - 4}" y="${y(l.v) - 5}" text-anchor="end">${l.label} ${l.v}</text>`).join("");
  const empty = vals.length ? "" : `<text class="ns-axis" x="${m.l + iw / 2}" y="${m.t + ih / 2}" text-anchor="middle">No readings yet</text>`;

  fig.innerHTML = `
    <figcaption><b>${spec.title} (${spec.unit})</b><span>last 10 min</span></figcaption>
    <div class="ns-chart-plot">
      <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img"
           aria-label="${spec.title}, last 10 minutes${latest === null ? "" : `, now ${latest.toFixed(spec.digits)} ${spec.unit}`}">
        ${grid}${thr}${xlab}${empty}
        <path class="ns-line" d="${d}"/>
        <line class="ns-cross" x1="0" x2="0" y1="${m.t}" y2="${m.t + ih}" visibility="hidden"/>
        <circle class="ns-cross-dot" r="4.5" visibility="hidden"/>
        <rect class="ns-hit" x="${m.l}" y="0" width="${iw}" height="${H}"/>
      </svg>
      <div class="ns-tip" hidden></div>
    </div>`;

  // Hover: crosshair + tooltip on the nearest reading
  const svg = fig.querySelector("svg"), tip = fig.querySelector(".ns-tip");
  const cross = fig.querySelector(".ns-cross"), dot = fig.querySelector(".ns-cross-dot");
  const valid = pts.filter((p) => p[key] !== null && p[key] !== undefined);
  const hide = () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); };
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("pointermove", (ev) => {
    if (!valid.length) return;
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    let best = valid[0];
    for (const p of valid) if (Math.abs(x(p.t) - px) < Math.abs(x(best.t) - px)) best = p;
    const bx = x(best.t), by = y(best[key]);
    cross.setAttribute("x1", bx); cross.setAttribute("x2", bx); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", bx); dot.setAttribute("cy", by); dot.setAttribute("visibility", "visible");
    tip.innerHTML = `<b>${best[key].toFixed(spec.digits)} ${spec.unit}</b><span>${nsClock(best.t)}</span>`;
    tip.hidden = false;
    const left = (bx / W) * r.width;
    tip.style.left = `${Math.min(Math.max(left, 50), r.width - 50)}px`;
    tip.style.top = `${(by / H) * r.height}px`;
  });
}

function nsClock(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function nsEnvTable(el, points) {
  if (!el || !el.closest("details").open) return;
  const rows = points.slice(-15).reverse().map((p) =>
    `<tr><td>${nsClock(p.t)}</td><td>${nsFmt(p.temp_c, 1)}</td><td>${nsFmt(p.pressure_hpa, 1)}</td><td>${nsFmt(p.gas_mv, 0)}</td>`
    + `<td>${p.gas_ratio === null || p.gas_ratio === undefined ? "–" : Math.round(p.gas_ratio * 100)}</td></tr>`).join("");
  el.innerHTML = `<table><thead><tr><th>Time</th><th>Temp (°C)</th><th>Pressure (hPa)</th><th>Gas (mV)</th><th>Gas (% clean air)</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="5">No readings yet</td></tr>'}</tbody></table>`;
}

document.addEventListener("toggle", (e) => { if (e.target.matches && e.target.matches(".ns-env-table")) nsDrawEnvCharts(); }, true);
window.addEventListener("resize", () => nsDrawEnvCharts());
window.addEventListener("hashchange", () => { nsEnvFetchedAt = 0; });
