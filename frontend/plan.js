// Site plan drawer, used by the Setup view's "Machines & site" preview to
// check positions and sensor facing angles. (The Site view no longer draws
// sensor cones - it shows machine status cards instead.)
//
// nsDrawPlan(canvas, wrap, room, machines, people, opts)

"use strict";

(function () {
const NS_COLOR = { ok: "#22c55e", warn: "#f59e0b", danger: "#ef4444", offline: "#7b8794" };
const NS_MAP_FONT = '"Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif';
const NS_ZONE_LEVEL = { danger: "danger", warning: "warn", clear: "ok" };
const NS_CONE_HALF_DEG = 10;       // HC-SR04 beam: roughly 20 degrees wide

const NS_PAD = 36;                 // room for the grid bubbles around the plan
const nsEased = {};                // smoothly growing ring radii per canvas+machine

function nsRoundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function nsLabel(ctx, text, x, y, opts = {}) {
  ctx.font = `${opts.weight || 700} ${opts.size || 15}px ${NS_MAP_FONT}`;
  ctx.textAlign = opts.align || "center";
  ctx.textBaseline = opts.baseline || "top";
  if (opts.pill) {
    const tw = ctx.measureText(text).width;
    const padX = 7, hgt = (opts.size || 15) + 9;
    const left = opts.align === "left" ? x - padX : x - tw / 2 - padX;
    ctx.fillStyle = opts.pill;
    nsRoundRect(ctx, left, y - 4, tw + padX * 2, hgt, 4);
    ctx.fill();
    ctx.fillStyle = opts.color || "#0b1220";
    ctx.fillText(text, x, y + 1);
    return;
  }
  ctx.lineWidth = 4;
  ctx.strokeStyle = "rgba(10,13,16,0.92)";
  ctx.strokeText(text, x, y);
  ctx.fillStyle = opts.color || "#eef1f4";
  ctx.fillText(text, x, y);
}

function nsEase(key, target) {
  const cur = nsEased[key];
  const next = cur === undefined ? target : cur + (target - cur) * 0.35;
  nsEased[key] = Math.abs(next - target) < 0.002 ? target : next;
  return nsEased[key];
}

// canvas/wrap: where to draw; room {width,height} in m; machines from
// state.site.machines (or the Setup preview); people: [{machine, dist_cm,
// name, level, pulse}]
function nsDrawPlan(canvas, wrap, room, machines, people, opts = {}) {
  const wrapW = wrap.clientWidth;
  if (wrapW < 50) return;
  const ctx = canvas.getContext("2d");
  const maxH = Math.max(window.innerHeight * (opts.preview ? 0.5 : 0.62), 300);
  let s = (wrapW - NS_PAD * 2) / room.width;
  if (s * room.height + NS_PAD * 2 > maxH) s = (maxH - NS_PAD * 2) / room.height;
  const w = wrapW, h = s * room.height + NS_PAD * 2;
  const dpr = window.devicePixelRatio || 1;
  canvas.style.height = `${h}px`;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const offX = (w - room.width * s) / 2;
  const P = (x, y) => [offX + x * s, NS_PAD + (room.height - y) * s];   // metres -> px (y up)

  // Floor, 1 m grid, construction-drawing grid bubbles, fence, scale, north
  const [x0, y0] = P(0, room.height);
  const W = room.width * s, H = room.height * s;
  ctx.fillStyle = "#11161b";
  ctx.fillRect(x0, y0, W, H);
  const step = Math.max(room.width, room.height) > 30 ? 5 : 1;
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(255,255,255,0.07)";
  for (let x = step; x < room.width; x += step) {
    const [px] = P(x, 0);
    ctx.beginPath(); ctx.moveTo(px, y0); ctx.lineTo(px, y0 + H); ctx.stroke();
  }
  for (let y = step; y < room.height; y += step) {
    const [, py] = P(0, y);
    ctx.beginPath(); ctx.moveTo(x0, py); ctx.lineTo(x0 + W, py); ctx.stroke();
  }
  const bubble = (bx, by, text) => {
    ctx.fillStyle = "#0a0d10";
    ctx.strokeStyle = "#6f7a86";
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(bx, by, 10, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#a3adb8";
    ctx.font = `700 11px ${NS_MAP_FONT}`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(text, bx, by + 1);
  };
  for (let x = 0, i = 0; x <= room.width + 1e-9; x += step, i++) bubble(P(x, 0)[0], y0 - 18, i < 26 ? String.fromCharCode(65 + i) : String(i + 1));
  for (let y = 0, i = 0; y <= room.height + 1e-9; y += step, i++) bubble(x0 - 18, P(0, y)[1], String(i + 1));
  ctx.strokeStyle = "#5b6672";
  ctx.lineWidth = 3;
  ctx.strokeRect(x0, y0, W, H);
  ctx.save();
  ctx.setLineDash([12, 8]);
  ctx.strokeStyle = "rgba(255,204,0,0.45)";
  ctx.lineWidth = 2;
  ctx.strokeRect(x0 + 6, y0 + 6, W - 12, H - 12);
  ctx.restore();
  const [sbx, sby] = P(0.35, room.height - 0.35);
  ctx.strokeStyle = "#a3adb8"; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(sbx, sby); ctx.lineTo(sbx + step * s, sby); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(sbx, sby - 5); ctx.lineTo(sbx, sby + 5); ctx.moveTo(sbx + step * s, sby - 5); ctx.lineTo(sbx + step * s, sby + 5); ctx.stroke();
  nsLabel(ctx, step === 1 ? "1 m" : `${step} m`, sbx + (step * s) / 2, sby + 6, { size: 13, color: "#a3adb8" });
  const nx = x0 + W - 28, ny = y0 + 22;
  ctx.fillStyle = "#a3adb8";
  ctx.beginPath(); ctx.moveTo(nx, ny - 12); ctx.lineTo(nx + 8, ny + 10); ctx.lineTo(nx, ny + 5); ctx.lineTo(nx - 8, ny + 10); ctx.closePath(); ctx.fill();
  nsLabel(ctx, "N", nx, ny + 13, { size: 13, color: "#a3adb8" });

  // Rings and cones, clipped to the site
  ctx.save();
  ctx.beginPath(); ctx.rect(x0, y0, W, H); ctx.clip();
  for (const m of machines) {
    const [mx, my] = P(m.x, m.y);
    const t = m.thresholds;
    const healthy = m.sensor.health === "ok" && m.level !== "offline";
    const key = `${canvas.id}:${m.id}`;
    const rW = nsEase(`${key}:w`, t.warning_cm / 100) * s;
    const rD = nsEase(`${key}:d`, t.danger_cm / 100) * s;
    const rR = (m.range_cm / 100) * s;
    const zoneLvl = NS_ZONE_LEVEL[m.zone];

    // Base rings (faint, dashed) when strain has raised the zones
    if (t.raised) {
      ctx.save();
      ctx.setLineDash([3, 5]); ctx.lineWidth = 1.5;
      ctx.strokeStyle = "rgba(245,158,11,0.45)";
      ctx.beginPath(); ctx.arc(mx, my, (t.base_warning_cm / 100) * s, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = "rgba(239,68,68,0.45)";
      ctx.beginPath(); ctx.arc(mx, my, (t.base_danger_cm / 100) * s, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
    }
    // Green ring = sensor range, amber = warning distance, red = danger distance
    ctx.lineWidth = 2;
    ctx.strokeStyle = healthy ? "rgba(34,197,94,0.45)" : "rgba(123,135,148,0.35)";
    ctx.beginPath(); ctx.arc(mx, my, rR, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = zoneLvl === "warn" ? "rgba(245,158,11,0.16)" : "rgba(245,158,11,0.06)";
    ctx.beginPath(); ctx.arc(mx, my, rW, 0, Math.PI * 2); ctx.arc(mx, my, rD, 0, Math.PI * 2, true); ctx.fill();
    ctx.strokeStyle = healthy ? NS_COLOR.warn : NS_COLOR.offline;
    ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(mx, my, rW, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = zoneLvl === "danger" ? "rgba(239,68,68,0.32)" : "rgba(239,68,68,0.10)";
    ctx.beginPath(); ctx.arc(mx, my, rD, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = healthy ? NS_COLOR.danger : NS_COLOR.offline;
    ctx.beginPath(); ctx.arc(mx, my, rD, 0, Math.PI * 2); ctx.stroke();

    // Sensor cone (~20 degrees) in the facing direction. Canvas angles run
    // clockwise with y down, so the map angle is negated.
    const a = (-m.facing_deg * Math.PI) / 180, half = (NS_CONE_HALF_DEG * Math.PI) / 180;
    const coneColor = !healthy ? "rgba(123,135,148,0.22)"
      : zoneLvl === "danger" ? "rgba(239,68,68,0.40)" : zoneLvl === "warn" ? "rgba(245,158,11,0.34)" : "rgba(34,197,94,0.20)";
    ctx.fillStyle = coneColor;
    ctx.beginPath(); ctx.moveTo(mx, my); ctx.arc(mx, my, rR, a - half, a + half); ctx.closePath(); ctx.fill();
    ctx.save();
    if (!healthy) ctx.setLineDash([6, 5]);
    ctx.strokeStyle = healthy ? "rgba(238,241,244,0.55)" : "rgba(123,135,148,0.8)";
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(mx, my); ctx.arc(mx, my, rR, a - half, a + half); ctx.closePath(); ctx.stroke();
    ctx.restore();
  }
  ctx.restore();

  // Machine blocks and labels
  for (const m of machines) {
    const [mx, my] = P(m.x, m.y);
    const color = NS_COLOR[m.level] || NS_COLOR.ok;
    ctx.fillStyle = color;
    nsRoundRect(ctx, mx - 16, my - 16, 32, 32, 6); ctx.fill();
    ctx.strokeStyle = "#0a0d10"; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = m.level === "warn" ? "#111" : "#fff";
    ctx.font = `700 17px ${NS_MAP_FONT}`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(m.name.charAt(0).toUpperCase(), mx, my + 1);
    // label on the side away from the cone
    const away = ((m.facing_deg % 360) + 360) % 360;
    // label on the side away from the cone, unless that runs off the plan
    let below = away < 180;
    if (!below && my - 58 < y0 + 2) below = true;   // no room above: there is padding below
    const ly = below ? my + 22 : my - 58;
    nsLabel(ctx, m.name, mx, ly, { size: 16 });
    const sub = opts.preview ? `${(m.thresholds.warning_cm / 100).toFixed(2)} / ${(m.thresholds.danger_cm / 100).toFixed(2)} m · ${Math.round(m.facing_deg)}°`
      : m.sensor.health !== "ok" ? `${m.display_state.toUpperCase()} · SENSOR ${m.sensor.label.toUpperCase()}`
      : m.display_state.toUpperCase();
    nsLabel(ctx, sub, mx, ly + 20, { size: 13, color: opts.preview ? "#a3adb8" : (m.sensor.health !== "ok" ? NS_COLOR.offline : color) });
  }

  // Detected people along each cone
  const pulse = (Date.now() % 1000) / 1000;
  for (const p of people) {
    const m = p.machine;
    const a = (m.facing_deg * Math.PI) / 180;
    const d = p.dist_cm / 100;
    const [px, py] = P(m.x + d * Math.cos(a), m.y + d * Math.sin(a));
    const color = NS_COLOR[p.level];
    if (p.pulse) {
      ctx.strokeStyle = color; ctx.globalAlpha = 1 - pulse; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(px, py, 16 + pulse * 18, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = 1;
    }
    // person glyph: ring + head + shoulders
    ctx.fillStyle = color;
    ctx.beginPath(); ctx.arc(px, py, 15, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#f1f5f9";
    ctx.beginPath(); ctx.arc(px, py - 4, 4.2, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(px, py + 8, 7, Math.PI, 0); ctx.fill();
    nsLabel(ctx, `${p.name.toUpperCase()} · ${(p.dist_cm / 100).toFixed(2)} m`, px + 21, py - 11,
      { size: 14, align: "left", pill: "#f1f5f9", color: "#0b1220" });
  }
}


window.nsDrawPlan = nsDrawPlan;
})();
