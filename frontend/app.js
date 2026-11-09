// NearStop dashboard: connection, routing and the Setup view.
//
// Receives live state from the backend over a WebSocket (/ws, ~5 updates/s).
// The Site view (the product dashboard) is drawn by site.js; this file owns
// the shared connection and the Setup pages (Sensors, Machines & site, Tools).

"use strict";

let state = null;            // latest state from the server
let lastMessageAt = 0;       // when we last got a WebSocket message (ms)
let ws = null;
let reconnectDelay = 1000;

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function ago(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 60) return `${seconds.toFixed(1)} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return `${Math.floor(seconds / 3600)} h ago`;
}

function setMsg(el, text, kind) {
  el.textContent = text;
  el.className = "msg " + (kind || "");
}

function deviceById(id) {
  return state ? state.devices.find((d) => d.id === id) : null;
}

async function postJSON(path, body) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await resp.json().catch(() => ({}));
  return { ok: resp.ok, data };
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { reconnectDelay = 1000; };
  ws.onmessage = (event) => {
    try {
      state = JSON.parse(event.data);
    } catch (e) {
      console.warn("bad message from server", e);
      return;
    }
    lastMessageAt = Date.now();
    render();
  };
  ws.onclose = () => {
    ws = null;
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 10000);
  };
  ws.onerror = () => ws && ws.close();
}

setInterval(() => {
  const pill = $("ws-pill");
  const age = (Date.now() - lastMessageAt) / 1000;
  if (ws && ws.readyState === WebSocket.OPEN && age < 2) {
    pill.textContent = "Backend: live";
    pill.className = "pill pill-ok";
  } else if (lastMessageAt === 0) {
    pill.textContent = "Connecting…";
    pill.className = "pill";
  } else {
    pill.textContent = "Backend not reachable – retrying";
    pill.className = "pill pill-bad";
  }
}, 500);

// ---------------------------------------------------------------------------
// Views and tabs:  #site (default)  |  #setup/live  #setup/setup  #setup/tools
// ---------------------------------------------------------------------------
let currentTab = "live";
let currentView = "site";

function render() {
  if (!state) return;
  if (typeof siteRender === "function") siteRender();
  if (currentView !== "setup") return;
  renderHeader();
  renderNetwork();
  if (currentTab === "live") {
    renderRawMachines();
    renderVest();
  } else if (currentTab === "setup") {
    syncLayoutFromServer();
    drawPreview();
  } else if (currentTab === "tools") {
    renderConnection();
    renderSimulator();
    renderChecklist();
  }
}

let sitePage = "floor";      // site view page: "floor" (#site) or "monitor" (#monitor)

function showView(view) {
  currentView = view;
  $("site-view").hidden = view !== "site";
  $("setup-view").hidden = view !== "setup";
  if (view === "setup") showTab(currentTab);
  else {
    $("ns-main").hidden = sitePage !== "floor";
    $("ns-monitor").hidden = sitePage !== "monitor";
    $("ns-nav-floor").classList.toggle("active", sitePage === "floor");
    $("ns-nav-monitor").classList.toggle("active", sitePage === "monitor");
    history.replaceState(null, "", sitePage === "monitor" ? "#monitor" : "#site");
    render();
  }
}

function route() {
  const [view, tab] = location.hash.slice(1).split("/");
  if (view === "setup") {
    if (["live", "setup", "tools"].includes(tab)) currentTab = tab;
    showView("setup");
  } else {
    sitePage = view === "monitor" ? "monitor" : "floor";
    showView("site");
  }
}

function showTab(name) {
  currentTab = name;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tab-page").forEach((p) => { p.hidden = p.id !== `tab-${name}`; });
  try { localStorage.setItem("nat-tab", name); } catch (e) { /* ignore */ }
  history.replaceState(null, "", `#setup/${name}`);
  render();
  if (name === "tools") { pollLogs(); refreshSerial(); }
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

// ---------------------------------------------------------------------------
// Setup header + network
// ---------------------------------------------------------------------------
function renderHeader() {
  $("sim-badge").hidden = !state.devices.some((d) => d.simulated && d.online);
  const online = state.devices.filter((d) => d.online).length;
  const pill = $("online-pill");
  pill.textContent = `Devices: ${online}/${state.devices.length} online`;
  pill.className = "pill " + (online === state.devices.length ? "pill-ok" : online ? "pill-warn" : "pill-bad");
}

function renderNetwork() {
  const n = state.network;
  const u = n.udp;
  const pill = $("udp-pill");
  if (!u.listening) {
    pill.textContent = "UDP: NOT listening";
    pill.className = "pill pill-bad";
  } else if (u.last_packet_ago !== null && u.last_packet_ago < 3) {
    pill.textContent = `UDP ${u.port}: receiving`;
    pill.className = "pill pill-ok";
  } else {
    pill.textContent = `UDP ${u.port}: listening, no packets`;
    pill.className = "pill pill-warn";
  }
  if (currentTab !== "tools") return;
  const byType = Object.entries(u.by_type).map(([k, v]) => `${esc(k)}: ${v}`).join(", ") || "–";
  const rows = [
    ["This computer's IP", esc(n.server_ips.join(", ") || "unknown")],
    ["UDP listener", u.listening ? `${esc(u.host)}:${u.port}` : `FAILED – ${esc(u.bind_error)}`],
    ["Packets received", `${u.packets_total} (${u.packets_malformed} malformed)`],
    ["By type", byType],
    ["Last packet", u.last_packet_ago === null ? "never" : `${ago(u.last_packet_ago)} from ${esc(u.last_packet_from)}`],
    ["Last error", u.last_error ? `${esc(u.last_error)} (${ago(u.last_error_ago)})` : "none"],
    ["Dashboards open", n.websocket_clients],
  ];
  $("network-info").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

// ---------------------------------------------------------------------------
// Sensors tab: raw machine data + vest
// ---------------------------------------------------------------------------
function renderRawMachines() {
  const body = $("raw-machines").querySelector("tbody");
  body.innerHTML = state.site.machines.map((m) => {
    const dev = deviceById(m.id);
    const r = dev && dev.machine;
    const hcls = { ok: "ok", no_reading: "warn", error: "bad" }[m.sensor.health];
    const cmd = m.last_command
      ? `<code>${esc(JSON.stringify(m.last_command.payload))}</code> ${ago(m.last_command.age_s)}${m.last_command.sent ? "" : " (not sent: no address yet)"}`
      : "–";
    const th = m.thresholds;
    const sync = m.zones_synced === null ? "" : m.zones_synced ? " ✓" : " (not confirmed)";
    return `<tr>
      <td><b>${esc(m.id)}</b></td><td>${esc(m.name)}</td>
      <td class="${hcls}">${esc(m.sensor.label)}${m.sensor.health === "ok" ? "" : `<br><span class="muted">${esc(m.sensor.text)}</span>`}</td>
      <td class="num">${r ? r.dist_cm : "–"}</td>
      <td>${r && r.zone ? esc(r.zone) : "–"}</td>
      <td>${r && r.state ? esc(r.state) : "–"}</td>
      <td>${r && r.warning_cm !== undefined ? `${r.warning_cm} / ${r.danger_cm} cm${sync}` : "not reported"}</td>
      <td>${th.warning_cm} / ${th.danger_cm} cm${th.raised ? ` <span class="muted">(base ${th.base_warning_cm} / ${th.base_danger_cm})</span>` : ""}</td>
      <td>${r ? ago(r.age_s) : "never"}${dev && dev.ip ? `<br><span class="muted">${esc(dev.ip)}:${dev.port || ""}</span>` : ""}</td>
      <td class="cmd">${cmd}</td>
    </tr>`;
  }).join("");
}

function renderVest() {
  const w = state.site.workers[0];
  if (!w) { $("raw-vest").innerHTML = "<dt>Vest</dt><dd>none configured</dd>"; return; }
  const dev = deviceById(w.id);
  const t = (dev && dev.telemetry) || {};
  const rows = [
    ["ID / name", `${esc(w.id)} – ${esc(w.display_name)}`],
    ["Status", w.online ? "online" : (w.last_seen_ago === null ? "never seen" : `offline (${ago(w.last_seen_ago)})`)],
    ["IP", esc(dev && dev.ip ? dev.ip : "–")],
    ["Activity", esc(t.activity || "no sensor")],
    ["Heat", t.heat_c !== undefined ? `${t.heat_c} °C (strain ${Math.round(w.strain * 100)}%)` : "no sensor"],
    ["Fall flag", w.fall_active ? "<b>FALL</b>" : "no"],
    ["Closest detection", w.near ? `${esc(w.near.name)} at ${w.near.distance_m.toFixed(2)} m` : "none"],
  ];
  $("raw-vest").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

// ---------------------------------------------------------------------------
// Machines & site tab: editable layout with live preview
// ---------------------------------------------------------------------------
let editLayout = null;        // {width_m, height_m, machines: {id: {...}}}
let layoutDirty = false;
let lastServerLayout = "";

function layoutFromConfig(cfg) {
  const machines = {};
  for (const [id, m] of Object.entries(cfg.machines)) {
    machines[id] = { name: m.name, x: m.x, y: m.y, facing_deg: m.facing_deg,
                     warning_cm: m.warning_cm, danger_cm: m.danger_cm };
  }
  return { width_m: cfg.site.width_m, height_m: cfg.site.height_m, machines };
}

function syncLayoutFromServer() {
  if (layoutDirty || !state.site_config) return;
  const serverLayout = JSON.stringify(layoutFromConfig(state.site_config));
  if (serverLayout === lastServerLayout && editLayout) return;   // don't steal focus
  lastServerLayout = serverLayout;
  editLayout = JSON.parse(serverLayout);
  renderLayoutInputs();
}

function renderLayoutInputs() {
  $("site-w").value = editLayout.width_m;
  $("site-h").value = editLayout.height_m;
  $("machine-rows").innerHTML = Object.entries(editLayout.machines).map(([id, m]) => `
    <tr>
      <td><b>${esc(id)}</b></td><td>${esc(m.name)}</td>
      <td><input type="number" step="0.1" data-m="${esc(id)}" data-k="x" value="${m.x}"></td>
      <td><input type="number" step="0.1" data-m="${esc(id)}" data-k="y" value="${m.y}"></td>
      <td><input type="number" step="5" min="0" max="359" data-m="${esc(id)}" data-k="facing_deg" value="${m.facing_deg}"></td>
      <td><input type="number" step="5" min="20" max="400" data-m="${esc(id)}" data-k="warning_cm" value="${m.warning_cm}"></td>
      <td><input type="number" step="5" min="10" max="395" data-m="${esc(id)}" data-k="danger_cm" value="${m.danger_cm}"></td>
    </tr>`).join("");
}

function markLayoutDirty() {
  layoutDirty = true;
  setMsg($("layout-msg"), "Unsaved changes", "warn");
  drawPreview();
}

$("machine-rows").addEventListener("input", (e) => {
  const { m, k } = e.target.dataset;
  const value = parseFloat(e.target.value);
  if (!m || Number.isNaN(value)) return;
  editLayout.machines[m][k] = value;
  markLayoutDirty();
});
for (const [id, key] of [["site-w", "width_m"], ["site-h", "height_m"]]) {
  $(id).addEventListener("input", (e) => {
    const value = parseFloat(e.target.value);
    if (Number.isNaN(value) || value <= 0.5) return;
    editLayout[key] = value;
    markLayoutDirty();
  });
}

$("layout-save").addEventListener("click", async () => {
  if (!editLayout) return;
  const machines = {};
  for (const [id, m] of Object.entries(editLayout.machines)) {
    machines[id] = { x: m.x, y: m.y, facing_deg: m.facing_deg,
                     warning_cm: Math.round(m.warning_cm), danger_cm: Math.round(m.danger_cm) };
  }
  try {
    const { ok, data } = await postJSON("/api/site-config",
      { width_m: editLayout.width_m, height_m: editLayout.height_m, machines });
    if (!ok) {
      const detail = typeof data.detail === "string" ? data.detail
        : Array.isArray(data.detail) ? "a value is out of range (warning 20–400 cm, danger 10–395 cm)" : "invalid values";
      setMsg($("layout-msg"), `Not saved: ${detail}`, "bad");
      return;
    }
    layoutDirty = false;
    lastServerLayout = "";
    setMsg($("layout-msg"), "Saved to site_config.json – new distances sent to the machines", "ok");
  } catch (e) {
    setMsg($("layout-msg"), "Not saved: cannot reach the backend", "bad");
  }
});

$("layout-undo").addEventListener("click", () => {
  layoutDirty = false;
  lastServerLayout = "";
  setMsg($("layout-msg"), "", "");
  if (state) syncLayoutFromServer();
  drawPreview();
});

function drawPreview() {
  if (!editLayout || typeof nsDrawPlan !== "function") return;
  const machines = Object.entries(editLayout.machines).map(([id, m]) => ({
    id, name: m.name, x: m.x, y: m.y, facing_deg: m.facing_deg, level: "ok",
    display_state: "Preview", dist_cm: null, zone: null,
    sensor: { health: "ok" },
    thresholds: { warning_cm: m.warning_cm, danger_cm: m.danger_cm,
                  base_warning_cm: m.warning_cm, base_danger_cm: m.danger_cm, raised: false },
    range_cm: (state && state.site && state.site.sensor_range_cm) || 300,
  }));
  nsDrawPlan($("layout-preview"), $("layout-wrap"),
    { width: editLayout.width_m, height: editLayout.height_m }, machines, [], { preview: true });
}
window.addEventListener("resize", () => { if (currentView === "setup" && currentTab === "setup") drawPreview(); });

// ---------------------------------------------------------------------------
// Tools tab: simulator
// ---------------------------------------------------------------------------
function renderSimulator() {
  const sim = state.simulator;
  const pill = $("sim-state");
  if (sim.running) {
    pill.textContent = "Running";
    pill.className = "pill pill-warn";
    const o = sim.options;
    $("sim-detail").textContent = `${o.scenario === "demo" ? "NearStop demo" : o.scenario === "gas" ? "gas leak + heat" : "visiting each machine"} · for ${sim.uptime_s} s`
      + (o.flaky && o.flaky.length ? ` · silent: ${o.flaky.join(", ")}` : "") + (o.junk ? " · sending junk" : "");
  } else {
    pill.textContent = "Stopped";
    pill.className = "pill";
    $("sim-detail").textContent = "";
  }
  $("sim-start").textContent = sim.running ? "Restart with these options" : "Start simulator";
  $("sim-stop").disabled = !sim.running;
}

async function setSimulator(running) {
  const msg = $("sim-msg");
  const body = {
    running,
    noise: parseFloat($("sim-noise").value) || 0,
    flaky: [...document.querySelectorAll(".sim-flaky:checked")].map((c) => c.value),
    junk: $("sim-junk").checked,
    scenario: $("sim-scenario").value,
  };
  setMsg(msg, running ? "Starting…" : "Stopping…", "");
  try {
    const { ok } = await postJSON("/api/simulator", body);
    if (!ok) { setMsg(msg, "Failed: jitter must be 0–30 cm", "bad"); return; }
    setMsg(msg, running ? "Started – devices appear within ~1 s" : "Stopped – sensors show Error after 2 s", "ok");
  } catch (e) {
    setMsg(msg, "Failed: cannot reach the backend", "bad");
  }
}
$("sim-start").addEventListener("click", () => setSimulator(true));
$("sim-stop").addEventListener("click", () => setSimulator(false));

// ---------------------------------------------------------------------------
// Tools tab: checklist (computed from live data)
// ---------------------------------------------------------------------------
function renderChecklist() {
  const real = (id) => { const d = deviceById(id); return Boolean(d && d.online && !d.simulated); };
  const machines = state.site.machines;
  const realReporting = machines.filter((m) => real(m.id) && m.sensor.age_s !== null);
  const realOk = machines.filter((m) => real(m.id) && m.sensor.health === "ok");
  const vest = state.site.workers[0];
  const realDetect = machines.filter((m) => real(m.id) && m.dist_cm !== null);
  const simSeen = state.devices.filter((d) => d.simulated && d.packet_count > 0).length >= machines.length + 1;
  const items = [
    [1, "Backend running, UDP listener open", state.network.udp.listening, state.network.udp.bind_error || ""],
    [1, "Dashboard receiving live updates", true, ""],
    [1, "Simulator test: all machines and the vest seen", simSeen, "Start the simulator above once to check"],
    [2, "A real machine sensor is reporting", realReporting.length > 0, "Flash a machine with the HC-SR04 firmware"],
    [2, `All ${machines.length} real machine sensors OK`, realOk.length === machines.length,
      `${realOk.length}/${machines.length} OK`],
    [3, "Real vest online", Boolean(vest && real(vest.id)), "Power on Sam's vest"],
    [4, "A real machine detects a person", realDetect.length > 0, "Stand in front of a machine's sensor"],
  ];
  $("checklist").innerHTML = items.map(([stage, text, done, note]) => `
    <li class="${done ? "done" : ""}">
      <span class="tick">${done ? "✓" : stage}</span>
      <div><span class="stage">Stage ${stage}</span><br>${esc(text)}
        ${!done && note ? `<small>${esc(note)}</small>` : ""}</div>
    </li>`).join("");
}

// ---------------------------------------------------------------------------
// Tools tab: connection, discovery, USB serial monitor
// ---------------------------------------------------------------------------
function renderConnection() {
  const n = state.network;
  const rows = [
    ["This computer's IP", `<code>${esc(n.server_ips[0] || "unknown")}</code> (where boards send to)`],
    ["UDP port", `<code>${n.udp.port}</code> – ${n.udp.listening ? "listening" : "NOT listening"}`],
    ["Machines reporting", `${state.site.machines.filter((m) => m.sensor.health !== "error").length}/${state.site.machines.length}`],
    ["Last packet", n.udp.last_packet_ago === null ? "never" : `${ago(n.udp.last_packet_ago)} from ${esc(n.udp.last_packet_from)}`],
  ];
  $("conn-info").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

function renderDiscover() {
  const names = (state.site && state.site.names) || {};
  const table = $("discover-table");
  const devices = state.devices.slice().sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0));
  table.querySelector("tbody").innerHTML = devices.map((d) => {
    const known = d.id in names;
    const status = d.online ? "online" : d.last_seen === null ? "never seen" : "offline";
    return `<tr>
      <td><b>${esc(d.id)}</b></td>
      <td class="${known ? "" : "unknown"}">${known ? esc(names[d.id]) : "not in site_config.json"}</td>
      <td>${esc(d.role)}${d.simulated ? " (sim)" : ""}</td><td>${status}</td>
      <td>${esc(d.ip || "–")}</td><td>${esc(d.mac || "–")}</td>
      <td>${d.last_seen === null ? "never" : ago(d.last_seen_ago)}</td></tr>`;
  }).join("");
  table.hidden = false;
  const heard = devices.filter((d) => d.last_seen !== null).length;
  setMsg($("discover-msg"), `${heard} device${heard === 1 ? "" : "s"} heard, ${devices.filter((d) => d.online).length} online`, "ok");
}
$("discover-btn").addEventListener("click", () => { if (state) renderDiscover(); });

async function refreshSerial(selectPort) {
  try {
    const s = await (await fetch("/api/serial")).json();
    const sel = $("serial-port");
    const current = selectPort || sel.value || s.port;
    if (!s.available) {
      sel.innerHTML = "<option>pyserial not installed</option>";
      sel.disabled = true;
      setMsg($("serial-msg"), "Run: pip install pyserial", "warn");
      return;
    }
    sel.innerHTML = s.ports.length
      ? s.ports.map((p) => `<option value="${esc(p.device)}">${esc(p.device)} – ${esc(p.chip || p.description)}${p.likely_esp32 ? " (ESP32?)" : ""}</option>`).join("")
      : "<option value=''>No serial ports found</option>";
    if (current && s.ports.some((p) => p.device === current)) sel.value = current;
    $("serial-connect").disabled = !s.ports.length || s.open;
    $("serial-disconnect").disabled = !s.open;
    if (s.open) setMsg($("serial-msg"), `Connected to ${s.port} – ${s.lines} lines (see Log)`, "ok");
    else if (s.error) setMsg($("serial-msg"), s.error, "bad");
    else setMsg($("serial-msg"), "Not connected", "");
  } catch (e) {
    setMsg($("serial-msg"), "Cannot reach the backend", "bad");
  }
}
$("serial-refresh").addEventListener("click", () => refreshSerial());
$("serial-connect").addEventListener("click", async () => {
  const port = $("serial-port").value;
  if (!port) return;
  const { ok, data } = await postJSON("/api/serial/open", { port, baud: parseInt($("serial-baud").value, 10) });
  if (!ok) { setMsg($("serial-msg"), data.detail || "Could not connect", "bad"); return; }
  refreshSerial(port);
});
$("serial-disconnect").addEventListener("click", async () => {
  await fetch("/api/serial/close", { method: "POST" });
  refreshSerial();
});
setInterval(() => { if (currentView === "setup" && currentTab === "tools") refreshSerial(); }, 3000);

// ---------------------------------------------------------------------------
// Tools tab: log (polled once per second while the tab is open)
// ---------------------------------------------------------------------------
let lastLogId = 0;
let logPolling = false;

function logClass(text) {
  if (text.startsWith("[sim]")) return "sim";
  if (text.startsWith("[serial")) return "ser";
  if (/error|fail|malformed|traceback/i.test(text)) return "err";
  if (/online|listening|started/i.test(text)) return "ok";
  return "";
}

async function pollLogs() {
  if (logPolling || currentView !== "setup" || currentTab !== "tools") return;
  logPolling = true;
  try {
    const lines = await (await fetch(`/api/logs?since=${lastLogId}`)).json();
    if (lines.length) {
      const pre = $("log");
      pre.insertAdjacentHTML("beforeend", lines.map((l) => {
        const t = new Date(l.time * 1000).toLocaleTimeString();
        return `<div><span class="t">${t}</span> <span class="${logClass(l.text)}">${esc(l.text)}</span></div>`;
      }).join(""));
      lastLogId = lines[lines.length - 1].id;
      while (pre.childElementCount > 1000) pre.removeChild(pre.firstElementChild);
      if ($("log-follow").checked) pre.scrollTop = pre.scrollHeight;
    }
  } catch (e) {
    // backend unreachable; the header pill already shows that
  }
  logPolling = false;
}
setInterval(pollLogs, 1000);
$("log-clear").addEventListener("click", () => { $("log").innerHTML = ""; });

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
try {
  const saved = localStorage.getItem("nat-tab");
  if (["live", "setup", "tools"].includes(saved)) currentTab = saved;
} catch (e) {
  // storage unavailable; start on the first tab
}
window.addEventListener("hashchange", route);
// site.js loads after this file; start once everything is defined
window.addEventListener("DOMContentLoaded", () => { route(); connect(); });
