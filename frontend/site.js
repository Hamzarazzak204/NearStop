// NearStop site view.
//
// Uses the live state that app.js receives over the WebSocket (`state`,
// `ws`, `lastMessageAt`, `currentView`, `esc`, `$` are defined there).
// Everything NearStop-specific is in state.site (built by backend/safety.py).
//
// The site view shows only what a supervisor needs, in plain words:
// each machine is "Clear", "Worker nearby" or "Too close". Raw distances,
// thresholds and sensor details live in the Setup view.

"use strict";

const NS_SITE = {               // overall status -> word + colour
  clear: ["All clear", "ok"], warning: ["Caution", "warn"], emergency: ["Emergency", "danger"],
};
const NS_PROX = {               // machine zone -> word + colour
  danger: ["Too close", "danger"], warning: ["Worker nearby", "warn"], clear: ["Clear", "ok"],
};

const NS_FALL_BANNER_S = 8;       // the red "Fall" banner stays this long after any fall
let nsLastEventId = -1;
let nsCheckinHidden = {};          // worker id -> check-in start time the user hid
let nsCheckinDeadline = null;      // local clock deadline for a smooth countdown
let nsCheckinWorker = null;
let nsSilencedKey = null;          // emergency the user silenced
let nsAlarmKey = null;             // current emergency (null = none)
let nsAudio = null;
let nsSoundOn = true;
try { nsSoundOn = localStorage.getItem("ns-sound") !== "off"; } catch (e) { /* default on */ }

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function nsConnected() {
  return ws && ws.readyState === WebSocket.OPEN && Date.now() - lastMessageAt < 3000;
}

function nsTime(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function nsAgo(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return `${Math.floor(seconds / 3600)} h ago`;
}

function nsCap(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

function nsIcon(id, cls = "") {
  return `<svg class="ns-ico ${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;
}

function nsMachineIcon(m) {
  const n = m.name.toLowerCase();
  if (/robot|arm|weld/.test(n)) return "i-robot";
  if (/conveyor|belt/.test(n)) return "i-conveyor";
  if (/hoist|crane|lift/.test(n)) return "i-hoist";
  return "i-machine";
}

// Proximity in plain words (no distances): Clear / Worker nearby / Too close
function nsProximity(m) {
  if (m.display_state === "Offline") return ["Offline", "offline"];
  if (m.sensor.health !== "ok") return ["No signal", "offline"];
  if (m.sam_near === true) return ["Sam nearby", "warn"];     // backend: strap ≥ near_rssi_dbm, or heard + ultrasonic sees him
  if (m.sam_near === undefined && m.worker_near === true && (m.worker_rssi ?? -127) >= (m.near_rssi_dbm ?? -127))
    return ["Sam nearby", "warn"];                             // older backend
  if (m.worker_near !== null && m.worker_near !== undefined) return NS_PROX.clear;   // strap not heard = clear
  return NS_PROX[m.zone] || NS_PROX.clear;
}

// Short worker status: SOS / Check-in / Fall / Too close / Nearby / OK / Offline
function nsWorkerStatus(w) {
  if (!w.online) return ["Offline", "offline"];
  if (w.checkin && w.checkin.status === "emergency") return ["SOS", "danger"];
  if (w.checkin) return ["Check-in", "warn"];
  if (w.fall_active) return ["Fall", "danger"];
  if (w.near && w.near.zone === "danger") return ["Too close", "danger"];
  if (w.near && w.near.zone === "warning") return ["Nearby", "warn"];
  return ["OK", "ok"];
}

function nsWorkerWhere(w) {
  if (!w.online) return w.last_seen_ago === null ? "Vest not connected" : `Vest offline · ${nsAgo(w.last_seen_ago)}`;
  if (w.near && (w.near.zone === "warning" || w.near.zone === "danger")) return `Near ${w.near.name}`;
  return "On site";
}

// Short reason why a restart is blocked (the backend's text has distances)
function nsBlockedShort(reason) {
  const r = (reason || "").toLowerCase();
  if (r.includes("evacuation")) return "Evacuation in progress";
  if (r.includes("emergency")) return "Emergency still active";
  if (r.includes("sensor")) return "No sensor signal";
  if (r.includes("offline")) return "Machine offline";
  if (r.includes("zone") || r.includes("person")) return "Worker still nearby";
  return "Not clear yet";
}

let nsToastTimer = null;
function nsToast(text) {
  const el = $("ns-toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(nsToastTimer);
  nsToastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

async function nsPost(path, body, okText) {
  try {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      nsToast(typeof data.detail === "string" ? nsBlockedShort(data.detail) : "That didn't work – try again");
      return false;
    }
    if (okText) nsToast(okText);
    return true;
  } catch (e) {
    nsToast("Can't reach NearStop");
    return false;
  }
}

// Update an element's HTML only when it changed. Re-creating buttons 5 times
// a second would swallow clicks, so cards keep their buttons in a separate
// region that only changes when the button itself changes.
function nsSetHTML(el, html) {
  if (el._nsHTML !== html) {
    el.innerHTML = html;
    el._nsHTML = html;
  }
}

function nsKeyedCards(container, items, keyOf, bodyOf, actionsOf, levelOf, cls = "ns-item") {
  const seen = new Set();
  for (const item of items) {
    const key = keyOf(item);
    seen.add(key);
    let card = container.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!card) {
      card = document.createElement("article");
      card.dataset.key = key;
      card.innerHTML = '<div class="ns-item-body"></div><div class="ns-actions"></div>';
      container.appendChild(card);
    }
    card.className = `${cls} lvl-${levelOf(item)}`;
    nsSetHTML(card.querySelector(".ns-item-body"), bodyOf(item));
    nsSetHTML(card.querySelector(".ns-actions"), actionsOf(item));
  }
  for (const card of [...container.children]) {
    if (!card.dataset.key || !seen.has(card.dataset.key)) card.remove();
  }
}

// ---------------------------------------------------------------------------
// Main render (called by app.js on every WebSocket update, ~5x per second)
// ---------------------------------------------------------------------------
function siteRender() {
  if (currentView !== "site" || !state || !state.site) return;
  const site = state.site;
  nsRenderTop(site);
  nsRenderBanner(site);
  nsRenderHero(site);
  nsRenderTiles(site);
  nsRenderOnSite(site);
  nsRenderWorkers(site);
  nsRenderMachines(site);
  nsRenderTimeline(site);
  nsRenderCheckinModal(site);
  if (typeof nsRenderMonitor === "function") nsRenderMonitor(site);   // Monitoring page (monitor.js)
}

// ---------------------------------------------------------------------------
// Top bar
// ---------------------------------------------------------------------------
function nsRenderTop(site) {
  const connected = nsConnected();
  const status = $("ns-status");
  if (!connected) {
    status.className = "ns-status lvl-offline";
    $("ns-status-text").textContent = lastMessageAt ? "Reconnecting…" : "Connecting…";
  } else {
    const [word, level] = NS_SITE[site.status.level];
    status.className = `ns-status lvl-${level}`;
    $("ns-status-text").textContent = word;
  }

  // Hub dot: is data from the boards arriving?
  const udp = state.network.udp;
  const receiving = connected && udp.last_packet_ago !== null && udp.last_packet_ago < 3;
  $("ns-hub").className = `ns-chip lvl-${!connected ? "offline" : receiving ? "ok" : udp.last_packet_ago === null ? "offline" : "danger"}`;
  $("ns-hub-text").textContent = "Hub";

  $("ns-sim").hidden = !state.devices.some((d) => d.simulated && d.online);

  const active = site.evacuation.active;
  $("ns-evac-label").textContent = active ? "End evacuation" : "Evacuate";
  $("ns-evacuate").classList.toggle("active", active);

  const sound = $("ns-sound");
  sound.classList.toggle("muted", !nsSoundOn);
  sound.title = sound.ariaLabel = nsSoundOn ? "Alarm sound on" : "Alarm sound off";
}

// ---------------------------------------------------------------------------
// Emergency banner + alarm sound
// ---------------------------------------------------------------------------
function nsRenderBanner(site) {
  const banner = $("ns-banner");
  const emergencies = site.workers.filter((w) => w.checkin && w.checkin.status === "emergency");
  let title = "", text = "", actions = "", key = null;

  if (site.evacuation.active) {
    key = `evac-${site.evacuation.since}`;
    title = "Evacuation";
    text = "All machines locked. Everyone to the assembly point.";
    actions = '<button class="ns-btn" data-ns="end-evac" type="button">End evacuation</button>';
  } else if (emergencies.length) {
    key = emergencies.map((w) => `${w.id}-${w.checkin.started}`).join("|");
    const w = emergencies[0];
    const stopped = site.machines.filter((m) => m.latched && /emergency/.test(m.reason)).map((m) => m.name);
    title = "SOS";
    text = `${w.name} not responding${w.checkin.machine_name ? ` near ${w.checkin.machine_name}` : ""}.`
      + (stopped.length ? ` ${stopped.join(", ")} stopped.` : "") + " Send help now.";
    actions = emergencies.map((e) =>
      `<button class="ns-btn" data-ns="resolve-emergency" data-worker="${esc(e.id)}" type="button">${esc(e.name)} is safe</button>`).join("");
  } else {
    // A worker fell near a machine in the last 2 minutes and it is still locked out
    const fell = site.workers.find((w) => w.last_fall && w.last_fall.ago_s < 120
      && site.machines.some((m) => w.last_fall.machines.includes(m.name) && m.display_state === "Locked"));
    // Any other fall (e.g. away from every machine): alert while the check-in is open
    const ci = site.workers.find((w) => w.checkin && w.checkin.status === "pending" && w.checkin.reason === "fall");
    if (fell) {
      const f = fell.last_fall;
      key = `fall-${fell.id}-${f.t}`;
      title = "Fall";
      text = `${fell.name} fell near ${f.primary || f.machines[0]}. ${f.machines.join(", ")} stopped and locked out.`
        + " Check on them now.";
    } else if (ci) {
      key = `fallci-${ci.id}-${ci.checkin.started}`;
      title = "Fall";
      text = `${ci.name} fell${ci.checkin.machine_name ? ` near ${ci.checkin.machine_name}` : " (not near a machine)"}.`
        + " Check on them now. No answer = SOS.";
    } else {
      // Any fall in the last NS_FALL_BANNER_S seconds, wherever the worker was (even after "I'm OK" or a
      // supervisor's answer): the control hub must not miss it.
      const rf = site.workers.find((w) => w.recent_fall && w.recent_fall.ago_s < NS_FALL_BANNER_S);
      if (rf) {
        const f = rf.recent_fall;
        const mins = Math.floor(f.ago_s / 60), secs = Math.round(f.ago_s % 60);
        key = `fallany-${rf.id}-${f.t}`;
        title = "Fall";
        text = `${rf.name} fell ${mins ? `${mins} min ` : ""}${secs} s ago`
          + `${f.machine_name && f.zone !== "none" ? ` near ${f.machine_name}` : " (not near a machine)"}`
          + `${typeof f.peak_g === "number" ? `, ${f.peak_g.toFixed(1)} g impact` : ""}. Make sure they are OK.`;
      }
    }
  }

  nsAlarmKey = key;
  banner.hidden = !key;
  if (!key) return;
  const playing = nsSoundOn && nsSilencedKey !== key;
  if (playing) actions = '<button class="ns-btn" data-ns="silence" type="button">Silence</button>' + actions;
  if (playing && (!nsAudio || nsAudio.state !== "running")) text += " (Click anywhere for sound.)";
  $("ns-banner-title").textContent = title;
  $("ns-banner-text").textContent = text;
  nsSetHTML($("ns-banner-actions"), actions);
}

function nsUnlockAudio() {
  try {
    if (!nsAudio) nsAudio = new (window.AudioContext || window.webkitAudioContext)();
    if (nsAudio.state === "suspended") nsAudio.resume();
  } catch (e) { /* no audio available */ }
}
document.addEventListener("pointerdown", nsUnlockAudio);
document.addEventListener("keydown", nsUnlockAudio);

function nsBeep(freq, start, length) {
  const osc = nsAudio.createOscillator();
  const gain = nsAudio.createGain();
  osc.type = "square";
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(0.18, start + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + length);
  osc.connect(gain).connect(nsAudio.destination);
  osc.start(start);
  osc.stop(start + length + 0.02);
}

// Two-tone alarm every 1.2 s while an emergency is active (and not silenced)
setInterval(() => {
  if (!nsAlarmKey || !nsSoundOn || nsSilencedKey === nsAlarmKey) return;
  if (!nsAudio || nsAudio.state !== "running") return;
  const t = nsAudio.currentTime;
  nsBeep(880, t, 0.22);
  nsBeep(660, t + 0.28, 0.22);
}, 1200);

// ---------------------------------------------------------------------------
// Hero + stage: one large icon card per machine, placed where it stands
// ---------------------------------------------------------------------------
function nsRenderHero(site) {
  $("ns-eyebrow").textContent = `${site.name} · live`;
  const n = site.machines.length, online = site.workers.filter((w) => w.online).length;
  $("ns-hero-sub").textContent = `${n} machine${n === 1 ? "" : "s"} · ${online} on site`;
}

function nsTileHTML(m, workersHere) {
  const [prox] = nsProximity(m);
  const run = m.display_state === "Offline" ? "" : m.display_state;
  const extras = [];
  if (m.thresholds && m.thresholds.raised && m.display_state !== "Offline") {
    extras.push(`<span class="ns-badge" title="Caution distance increased because a worker is under strain">${nsIcon("i-shield")}Extra caution</span>`);
  }
  for (const w of workersHere) {
    const [, lvl] = nsWorkerStatus(w);
    extras.push(`<span class="ns-worker-chip lvl-${lvl}"><span class="ns-dot"></span>${esc(w.name)}</span>`);
  }
  return `
    <div class="ns-tile-icon">${nsIcon(nsMachineIcon(m))}${m.latched ? `<span class="ns-tile-lock">${nsIcon("i-lock")}</span>` : ""}</div>
    <div class="ns-tile-name">${esc(m.name)}</div>
    <div class="ns-tile-prox"><span class="ns-dot"></span>${prox}</div>
    ${run ? `<div class="ns-tile-run">${esc(run)}</div>` : ""}
    <div class="ns-tile-extras">${extras.join("")}</div>`;
}

function nsRenderTiles(site) {
  const box = $("ns-tiles");
  const room = site.room;
  const seen = new Set();
  for (const m of site.machines) {
    seen.add(m.id);
    let tile = box.querySelector(`[data-key="${CSS.escape(m.id)}"]`);
    if (!tile) {
      tile = document.createElement("article");
      tile.dataset.key = m.id;
      box.appendChild(tile);
    }
    const [, level] = nsProximity(m);
    tile.className = `ns-tile ns-glass lvl-${level}`;
    // place on the stage by site position (y up), kept inside the stage
    const px = 10 + (Math.min(Math.max(m.x / room.width, 0), 1)) * 80;
    const py = 16 + (1 - Math.min(Math.max(m.y / room.height, 0), 1)) * 66;
    tile.style.left = `${px}%`;
    tile.style.top = `${py}%`;
    const here = site.workers.filter((w) => w.online && w.near && w.near.machine_id === m.id
      && (w.near.zone === "warning" || w.near.zone === "danger"));
    nsSetHTML(tile, nsTileHTML(m, here));
  }
  for (const t of [...box.children]) if (!seen.has(t.dataset.key)) t.remove();
}

// "On site": workers online that aren't near any machine
function nsRenderOnSite(site) {
  const away = site.workers.filter((w) => w.online
    && !(w.near && (w.near.zone === "warning" || w.near.zone === "danger")));
  $("ns-onsite").hidden = !away.length;
  nsSetHTML($("ns-onsite-list"), away.map((w) => {
    const [word, lvl] = nsWorkerStatus(w);
    return `<span class="ns-worker-chip lvl-${lvl}"><span class="ns-dot"></span>${esc(w.name)}${word !== "OK" ? ` · ${word}` : ""}</span>`;
  }).join(""));
}

// ---------------------------------------------------------------------------
// Side panel: worker card + machine cards
// ---------------------------------------------------------------------------
function nsWorkerBody(w) {
  const [word] = nsWorkerStatus(w);
  const meta = [];
  if (w.online && w.heat_c !== null && w.heat_c !== undefined) meta.push(`<span title="Body heat">${nsIcon("i-heat")}${w.heat_c.toFixed(1)}°</span>`);
  if (w.online && w.activity) meta.push(`<span title="Activity">${nsIcon("i-walk")}${esc(w.activity)}</span>`);
  if (w.online && w.strap) {
    const zoneWord = { red: "Red", yellow: "Yellow", green: "Green", none: "No data" }[w.strap.zone] || "–";
    meta.push(`<span title="Strap light (LED on = close to a machine)">Strap ${zoneWord}${w.strap.near_led ? " · LED on" : ""}</span>`);
  }
  if (w.env && !w.env.stale) {      // strap BMP180 + MQ-2 (details on the Monitoring page)
    const e = w.env, gasWord = { warn: "Gas detected", danger: "High gas", warming: "Gas warming up" }[e.gas_level];
    if (e.temp_c !== null) meta.push(`<span title="Air temperature / pressure at the worker">${nsIcon("i-heat")}${e.temp_c.toFixed(0)}°`
      + `${e.pressure_hpa !== null ? ` · ${e.pressure_hpa.toFixed(0)} hPa` : ""}</span>`);
    if (gasWord) meta.push(`<span class="lvl-${e.gas_level === "warming" ? "info" : e.gas_level}" title="Combustible gas (MQ-2)">${nsIcon("i-flame")}<span class="ns-word">${gasWord}</span></span>`);
  }
  let fallLine = "";
  if (w.online && w.strap && w.strap.imu === false) {
    fallLine = `<div class="ns-item-line lvl-danger"><span class="ns-dot"></span>Fall detection OFF – motion sensor not answering (check its wires)</div>`;
  }
  if (w.last_fall && w.last_fall.ago_s < 600) {
    fallLine += `<div class="ns-item-line lvl-danger"><span class="ns-dot"></span>Fell near ${esc(w.last_fall.machines.join(", "))}`
      + ` ${nsAgo(w.last_fall.ago_s)} – locked out</div>`;
  }  else if (w.recent_fall && w.recent_fall.ago_s < 600) {
    const f = w.recent_fall;
    fallLine += `<div class="ns-item-line lvl-danger"><span class="ns-dot"></span>Fell ${nsAgo(f.ago_s)}`
      + `${f.machine_name && f.zone !== "none" ? ` near ${esc(f.machine_name)}` : " (not near a machine)"}`
      + `${typeof f.peak_g === "number" ? ` · ${f.peak_g.toFixed(1)} g` : ""}</div>`;
  }

  let count = "";
  if (w.checkin && w.checkin.status === "pending" && w.checkin.seconds_left !== null) {
    count = ` · ${Math.ceil(w.checkin.seconds_left)}`;
  }
  return `
    <div class="ns-row">
      <div class="ns-avatar">${nsIcon("i-worker")}</div>
      <div class="ns-item-main">
        <div class="ns-item-name">${esc(w.name)}</div>
        <div class="ns-item-line"><span class="ns-dot"></span><span class="ns-word">${word}${count}</span></div>
        <div class="ns-item-line">${esc(nsWorkerWhere(w))}</div>
        ${fallLine}
      </div>
    </div>
    ${meta.length ? `<div class="ns-meta">${meta.join("")}</div>` : ""}`;
}

function nsWorkerActions(w) {
  let label = `Check on ${esc(w.name)}`, disabled = false;
  if (!w.online) { label = "Vest offline"; disabled = true; }
  else if (w.checkin && w.checkin.status === "emergency") { label = "SOS active"; disabled = true; }
  else if (w.checkin) { label = "Checking…"; disabled = true; }
  return `<button class="ns-btn" data-ns="checkin" data-worker="${esc(w.id)}" type="button"${disabled ? " disabled" : ""}>${label}</button>`;
}

function nsRenderWorkers(site) {
  const box = $("ns-workers");
  if (!site.workers.length) {
    nsSetHTML(box, '<div class="ns-empty">No worker vests set up</div>');
    return;
  }
  if (box.querySelector(".ns-empty")) { box.innerHTML = ""; box._nsHTML = null; }
  nsKeyedCards(box, site.workers, (w) => w.id, nsWorkerBody, nsWorkerActions, (w) => nsWorkerStatus(w)[1]);
}

const NS_STATE_LEVEL = { Running: "ok", Slowed: "warn", Holding: "warn", Stopped: "danger", Locked: "danger", Offline: "offline" };

function nsMachineBody(m) {
  const [prox, plevel] = nsProximity(m);
  return `
    <div class="ns-row">
      <div class="ns-mini-icon lvl-${plevel}">${nsIcon(nsMachineIcon(m))}</div>
      <div class="ns-item-main">
        <div class="ns-item-name">${esc(m.name)}</div>
        <div class="ns-item-line lvl-${plevel}"><span class="ns-dot"></span>${prox}${m.worker_near && m.worker_rssi > -127
          ? ` · <span title="Signal strength of Sam's strap as heard by this machine, 2 s average (counts as near at ${m.near_rssi_dbm} dBm or stronger, or when the ultrasonic sensor also sees him)">${m.worker_rssi_avg ?? m.worker_rssi} dBm</span>` : ""}${m.sam_near_by === "ultrasonic" ? " · seen by sensor" : ""}</div>
      </div>
      <span class="ns-state lvl-${NS_STATE_LEVEL[m.display_state] || "offline"}">${esc(m.display_state)}</span>
    </div>
    ${m.display_state === "Locked" && /fell/.test(m.reason || "")
      ? `<div class="ns-item-line lvl-danger"><span class="ns-dot"></span>Worker fell here – locked out</div>` : ""}`;
}

function nsMachineActions(m) {
  if (!m.latched) return "";
  const blocked = m.can_restart ? "" : `<p class="ns-blocked">${esc(nsBlockedShort(m.restart_blocked_reason))}</p>`;
  return `<button class="ns-btn ns-btn-primary" data-ns="restart" data-machine="${esc(m.id)}" type="button"`
    + `${m.can_restart ? "" : " disabled"}>${nsIcon("i-check")} Approve restart</button>${blocked}`;
}

function nsRenderMachines(site) {
  const box = $("ns-machines");
  if (!site.machines.length) {
    nsSetHTML(box, '<div class="ns-empty">No machines set up</div>');
    return;
  }
  if (box.querySelector(".ns-empty")) { box.innerHTML = ""; box._nsHTML = null; }
  nsKeyedCards(box, site.machines, (m) => m.id, nsMachineBody, nsMachineActions,
    (m) => NS_STATE_LEVEL[m.display_state] || "offline");
}

// ---------------------------------------------------------------------------
// Activity: 5 newest events, short plain sentences
// ---------------------------------------------------------------------------
function nsRenderTimeline(site) {
  const events = site.events;
  const lastId = events.length ? events[events.length - 1].id : 0;
  if (lastId === nsLastEventId) return;
  nsLastEventId = lastId;
  const list = $("ns-timeline");
  if (!events.length) {
    list.innerHTML = '<li class="ns-empty" style="display:block">Nothing yet</li>';
    return;
  }
  list.innerHTML = events.slice(-5).reverse().map((e) => {
    const lvl = { ok: "ok", warn: "warn", danger: "danger" }[e.level] || "info";
    return `<li class="lvl-${lvl}"><time>${nsTime(e.t)}</time><span class="ns-dot"></span>`
      + `<span class="ns-msg">${esc(e.short || e.text)}</span></li>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// Check-in: "Waiting for Sam… 8"
// ---------------------------------------------------------------------------
function nsRenderCheckinModal(site) {
  const pending = site.workers.find((w) => w.checkin && w.checkin.status === "pending"
    && nsCheckinHidden[w.id] !== w.checkin.started);
  const modal = $("ns-modal-checkin");
  if (!pending) {
    modal.hidden = true;
    nsCheckinWorker = null;
    return;
  }
  const c = pending.checkin;
  const opening = modal.hidden || nsCheckinWorker !== pending.id;
  nsCheckinWorker = pending.id;
  nsCheckinDeadline = performance.now() + c.seconds_left * 1000;
  $("ns-ci-eyebrow").textContent = c.reason === "fall" ? "Possible fall" : "Check-in";
  $("ns-ci-title").textContent = `Waiting for ${pending.name}…`;
  $("ns-ci-hint").textContent = pending.simulated || pending.has_sensors
    ? `${pending.name} can press OK on the vest. No answer = SOS.`
    : "Vest has no OK button yet: confirm by radio or in person.";
  $("ns-ci-ok").textContent = `${pending.name} is OK`;
  modal.hidden = false;
  nsTickCountdown();
  // Focus the dialog itself, NOT the OK button: a stray Enter/Space key
  // press must never answer a safety check-in.
  if (opening) $("ns-ci-box").focus();
}

function nsTickCountdown() {
  if ($("ns-modal-checkin").hidden || nsCheckinDeadline === null) return;
  const left = Math.max(0, (nsCheckinDeadline - performance.now()) / 1000);
  const timeout = (state && state.site && state.site.checkin_timeout_s) || 10;
  $("ns-ci-count").textContent = Math.ceil(left);
  const w = state && state.site ? state.site.workers.find((x) => x.id === nsCheckinWorker) : null;
  const fellAt = w && w.checkin && w.checkin.reason === "fall" && w.last_fall && w.last_fall.ago_s < 60
    ? `Fell near ${w.checkin.machine_name || w.last_fall.primary} · ${w.last_fall.machines.join(", ")} stopped` : null;
  $("ns-ci-text").textContent = left > 0
    ? (fellAt || (w && w.checkin && w.checkin.machine_name ? `Near ${w.checkin.machine_name}` : "No reply yet"))
    : "No reply – raising SOS…";
  $("ns-ci-arc").style.strokeDashoffset = `${326.7 * (1 - Math.min(1, left / timeout))}`;
}
setInterval(nsTickCountdown, 100);

// ---------------------------------------------------------------------------
// Connection watchdog: "Reconnecting…" and dimmed data
// ---------------------------------------------------------------------------
setInterval(() => {
  if (currentView !== "site") return;
  const connected = nsConnected();
  $("ns-reconnect").hidden = connected;
  $("ns-main").classList.toggle("stale", !connected && lastMessageAt > 0);
  if (!connected) {
    $("ns-reconnect-text").textContent = lastMessageAt ? "Reconnecting… showing last known data" : "Connecting…";
    if (state && state.site) nsRenderTop(state.site);
    if (!lastMessageAt) {
      nsSetHTML($("ns-workers"), '<div class="ns-empty">Waiting for data…</div>');
      nsSetHTML($("ns-machines"), '<div class="ns-empty">Waiting for data…</div>');
    }
  }
}, 500);

// ---------------------------------------------------------------------------
// Buttons (event delegation so re-rendering never loses a click)
// ---------------------------------------------------------------------------
document.getElementById("site-view").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-ns]");
  if (!btn || btn.disabled) return;
  const action = btn.dataset.ns;
  if (action === "checkin") {
    btn.disabled = true;
    await nsPost("/api/checkin", { worker_id: btn.dataset.worker });
  } else if (action === "restart") {
    btn.disabled = true;
    const name = state.site.machines.find((m) => m.id === btn.dataset.machine)?.name || "Machine";
    await nsPost(`/api/machines/${encodeURIComponent(btn.dataset.machine)}/restart`, {}, `${name} restarted`);
  } else if (action === "resolve-emergency") {
    await nsPost("/api/emergency/resolve", { worker_id: btn.dataset.worker }, "Emergency resolved");
  } else if (action === "silence") {
    nsSilencedKey = nsAlarmKey;
    if (state && state.site) nsRenderBanner(state.site);
  } else if (action === "end-evac") {
    nsOpenEvacModal();
  }
});

$("ns-sound").addEventListener("click", () => {
  nsSoundOn = !nsSoundOn;
  try { localStorage.setItem("ns-sound", nsSoundOn ? "on" : "off"); } catch (e) { /* ignore */ }
  if (state && state.site) { nsRenderTop(state.site); nsRenderBanner(state.site); }
});

// Evacuation confirm dialog
function nsOpenEvacModal() {
  const active = state && state.site && state.site.evacuation.active;
  $("ns-evac-title").textContent = active ? "End evacuation?" : "Evacuate the site?";
  $("ns-evac-text").textContent = active
    ? "Machines stay locked until you approve each restart."
    : "All machines lock. They stay locked until you approve each restart.";
  const confirm = $("ns-evac-confirm");
  confirm.textContent = active ? "End evacuation" : "Evacuate";
  confirm.className = active ? "ns-btn ns-btn-primary" : "ns-btn ns-btn-danger";
  $("ns-modal-evac").hidden = false;
  $("ns-modal-evac").querySelector("[data-close]").focus();
}
$("ns-evacuate").addEventListener("click", nsOpenEvacModal);
$("ns-evac-confirm").addEventListener("click", async () => {
  const active = state && state.site && state.site.evacuation.active;
  $("ns-modal-evac").hidden = true;
  await nsPost("/api/evacuate", { active: !active }, active ? "Evacuation ended" : "Evacuation started");
});
$("ns-modal-evac").addEventListener("click", (e) => {
  if (e.target.matches("[data-close]") || e.target === e.currentTarget) $("ns-modal-evac").hidden = true;
});

// Check-in dialog
$("ns-ci-hide").addEventListener("click", () => {
  const w = state.site.workers.find((x) => x.id === nsCheckinWorker);
  if (w && w.checkin) nsCheckinHidden[w.id] = w.checkin.started;
  $("ns-modal-checkin").hidden = true;
});
function nsAnswerCheckin(outcome) {
  const w = state.site.workers.find((x) => x.id === nsCheckinWorker);
  if (w && w.checkin) nsCheckinHidden[w.id] = w.checkin.started;   // don't pop up again
  $("ns-modal-checkin").hidden = true;
  nsPost("/api/checkin/resolve", { worker_id: nsCheckinWorker, outcome },
    outcome === "ok" ? `${w ? w.name : "Worker"} is OK` : null);
}
$("ns-ci-ok").addEventListener("click", () => nsAnswerCheckin("ok"));
$("ns-ci-escalate").addEventListener("click", () => nsAnswerCheckin("escalate"));

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("ns-modal-evac").hidden) $("ns-modal-evac").hidden = true;
  else if (!$("ns-modal-checkin").hidden) $("ns-ci-hide").click();
});
