// M200 bridge web UI (laser build: no heaters). Messages: docs/protocol.md
"use strict";

const $ = (s) => document.querySelector(s);
const LOG_MAX = 500;
const HIST_MAX = 50;
const AXES = ["X", "Y", "Z"];

let ws = null;
let wsDelay = 500;
let state = {};
let step = 1;
// Settings kept by the bridge (Setting tweaking, road_Change.md ch. 6). Until its "settings"
// message arrives (bridge offline), the page uses the last ones it saw, else these defaults:
// key -> [default, min, max, step], a copy of SETTINGS[] in printer_state.c.
const DEFAULT_DESC = {
  max_x: [170, 10, 400, 1], max_y: [170, 10, 400, 1], max_z: [170, 10, 400, 1],
  park: [10, 1, 50, 1], backoff: [1, 0.2, 5, 0.1],
  xy_fast_f: [3000, 100, 6000, 100], xy_slow_f: [300, 30, 1500, 10],
  z_dir: [-1, -1, 1, 2], z_fast_step: [0.5, 0.1, 5, 0.1], z_slow_step: [0.1, 0.01, 1, 0.01],
  z_fast_f: [600, 30, 1500, 10], z_slow_f: [120, 10, 600, 10],
  jog_f_xy: [3000, 100, 6000, 100], jog_f_z: [600, 30, 1500, 10],
  preset_f: [3000, 100, 6000, 100], preset_edge: [10, 0, 40, 1],
};
let cfgDesc = DEFAULT_DESC;
let cfg = Object.fromEntries(Object.entries(DEFAULT_DESC).map(([k, d]) => [k, d[0]]));
let cfgLive = false;  // true once the bridge sent its settings on this connection
try {
  const saved = JSON.parse(localStorage.getItem("m200.settings") || "null");
  if (saved && saved.v) { Object.assign(cfg, saved.v); if (saved.d) cfgDesc = saved.d; }
} catch {}

// ---------- WebSocket ----------
function connect() {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onopen = () => { wsDelay = 500; setDot("#dot-ws", "ok"); };
  ws.onclose = () => {
    cfgLive = false;
    sheetOnline();
    setDot("#dot-ws", "bad");
    setDot("#dot-printer", "");
    setTimeout(connect, wsDelay);
    wsDelay = Math.min(wsDelay * 2, 8000);
  };
  ws.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.t === "state") onState(m);
    else if (m.t === "settings") onSettings(m);
    else if (m.t === "line") { addLine(m); sheetLine(m); }
    else if (m.t === "auth") onAuth(m);
    else if (m.t === "hello") {
      document.title = (m.name || "M200") + " Bridge";
      (m.history || []).forEach(addLine);
      if (m.auth === "required") login(false);
    }
  };
}

// ---------- Login (API token, set on the USB console with !token) ----------
// Viewing is open; commands need the token once one is set. STOP never needs it.
const TOKEN_KEY = "m200.token";
function savedToken() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } }
function login(ask) {
  let tok = ask ? "" : savedToken();
  if (!tok) {
    tok = window.prompt("This bridge needs its API token (set with !token on the USB console):") || "";
    if (!tok) { addLine({ dir: "rx", text: "Error: not logged in, commands are refused (STOP still works)" }); return; }
    try { localStorage.setItem(TOKEN_KEY, tok); } catch {}
  }
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ t: "auth", token: tok }));
}
function onAuth(m) {
  if (m.ok) { if (m.required) addLine({ dir: "rx", text: "echo: logged in" }); return; }
  try { localStorage.removeItem(TOKEN_KEY); } catch {}
  addLine({ dir: "rx", text: "Error: wrong or missing API token, command refused" });
  login(true);
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); return true; }
  addLine({ dir: "rx", text: "Error: not connected to the bridge" });
  return false;
}
// Why nothing may be sent now, or "".
function blocked() {
  if (state.halted) return "Error: printer halted (kill), power-cycle it first";
  if (state.stopped) return "Error: stopped, press RESTART first";
  if (state.homing) return "Error: homing running, wait for it";
  return "";
}
function gcode(...lines) {
  const why = blocked();
  if (why) { addLine({ dir: "rx", text: why }); return false; }
  return send({ t: "gcode", lines });
}

// ---------- State ----------
function setDot(sel, cls) {
  const el = $(sel);
  el.classList.remove("ok", "bad", "warn");
  if (cls) el.classList.add(cls);
}

const homed = (i) => !!(state.homed && state.homed[i]);
// Limit of axis i (0..max from its switch), and the sign of Marlin's numbers away from it.
const maxMm = (i) => (state.max && state.max[i]) || cfg[["max_x", "max_y", "max_z"][i]];
const dirOf = (i) => (state.dir && state.dir[i]) || 1;

function onState(s) {
  state = s;
  setDot("#dot-printer", s.link === "up" ? "ok" : s.link === "no_reply" ? "bad" : "");
  if (s.rssi == null) { $("#rssi").textContent = "wifi"; setDot("#dot-wifi", ""); }
  else {
    $("#rssi").textContent = s.rssi + " dBm";
    setDot("#dot-wifi", s.rssi > -67 ? "ok" : s.rssi > -78 ? "warn" : "bad");
  }
  showStopMode();

  const p = s.pos || [];
  ["x", "y", "z"].forEach((a, i) => {
    $("#pos-" + a).textContent = p[i] == null ? "--" : p[i].toFixed(2);
    $("#homed-" + a).classList.toggle("on", homed(i));
  });
  $("#busy").textContent = s.homing ? "homing..." : s.busy ? "busy..." : s.queue ? s.queue + " queued" : "";

  updateJog();
  updatePresets();
}

// ---------- Console ----------
const log = $("#log");
const showAll = $("#show-all");

function lineClass(m) {
  if (m.dir === "tx") return "tx";
  const t = m.text;
  if (/^(Error|!!)/.test(t)) return "err";
  if (/^ok\s*$/.test(t) || /^\s*T:[\d.]+\s*\//.test(t) || /^(echo:)?busy:/.test(t)) return "noise";
  if (/^echo:/.test(t)) return "echo";
  return "";
}

function addLine(m) {
  const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  const div = document.createElement("div");
  div.className = lineClass(m);
  div.textContent = m.text;
  if (m.dir === "tx" && m.src && m.src !== "web") {
    const s = document.createElement("span");
    s.className = "src"; s.textContent = m.src;
    div.appendChild(s);
  }
  log.appendChild(div);
  while (log.childElementCount > LOG_MAX) log.firstElementChild.remove();
  if (stick) log.scrollTop = log.scrollHeight;
}

function setQuiet() {
  log.classList.toggle("quiet", !showAll.checked);
  try { localStorage.setItem("m200.showAll", showAll.checked ? "1" : "0"); } catch {}
  log.scrollTop = log.scrollHeight;
}
try { showAll.checked = localStorage.getItem("m200.showAll") === "1"; } catch {}
showAll.addEventListener("change", setQuiet);
setQuiet();

let hist = [];
try { hist = JSON.parse(localStorage.getItem("m200.hist") || "[]"); } catch {}
let histPos = hist.length;
const cmdIn = $("#cmd-in");

function sendTyped(text) {
  const lines = text.split(/[\n;]/).map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return;
  if (lines.some((l) => /^M112\b/i.test(l))) { send({ t: "stop" }); return; }
  if (!gcode(...lines.map((l) => l.toUpperCase()))) return;
  if (hist[hist.length - 1] !== text) hist.push(text);
  hist = hist.slice(-HIST_MAX);
  histPos = hist.length;
  try { localStorage.setItem("m200.hist", JSON.stringify(hist)); } catch {}
}

$("#cmd").addEventListener("submit", (e) => {
  e.preventDefault();
  sendTyped(cmdIn.value);
  cmdIn.value = "";
});
cmdIn.addEventListener("keydown", (e) => {
  if (e.key === "ArrowUp" && histPos > 0) { histPos--; cmdIn.value = hist[histPos]; e.preventDefault(); }
  else if (e.key === "ArrowDown") {
    histPos = Math.min(hist.length, histPos + 1);
    cmdIn.value = hist[histPos] || "";
    e.preventDefault();
  }
});
document.querySelectorAll("[data-cmd]").forEach((b) => b.addEventListener("click", () => gcode(b.dataset.cmd)));
$("#clear").addEventListener("click", () => { log.textContent = ""; });

// ---------- Jog ----------
document.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => {
  step = Number(b.dataset.step);
  document.querySelectorAll("[data-step]").forEach((o) => o.classList.toggle("on", o === b));
}));
// "move without homing": not remembered across reloads on purpose.
const freeMove = $("#free-move");
freeMove.addEventListener("change", updateJog);

function updateJog() {
  const s = state;
  const live = s.link === "up" && !s.stopped && !s.halted && !s.homing;
  const all = homed(0) && homed(1) && homed(2);
  document.querySelectorAll("[data-jog]").forEach((b) => {
    b.disabled = !live || !(homed(AXES.indexOf(b.dataset.jog)) || freeMove.checked);
  });
  document.querySelectorAll("#home, #home-z").forEach((b) => { b.disabled = !live; });
  $("#jog-hint").textContent = s.halted ? "Printer halted: power-cycle it"
    : s.stopped ? "Stopped: press RESTART"
    : s.homing ? "Homing..."
    : freeMove.checked && !all ? "Not homed axes: no limits, watch the head"
    : all ? "" : "Home XY and Z (house buttons) to move";
}

document.querySelectorAll("[data-jog]").forEach((b) => b.addEventListener("click", () => {
  const axis = b.dataset.jog;
  const i = AXES.indexOf(axis);
  let d = step * Number(b.dataset.dir);
  // Homed: stay inside 0..max from the switch (the bridge refuses anything outside). d is in
  // Marlin's numbers, the machine position counts away from the switch (dir -1: the other way).
  if (homed(i) && state.mpos && state.mpos[i] != null) {
    const m = state.mpos[i];
    const k = dirOf(i);
    const to = Math.min(maxMm(i), Math.max(0, m + k * d));
    d = Math.round(k * (to - m) * 1000) / 1000;
    if (!d) { addLine({ dir: "rx", text: `${axis} is at its limit (0..${maxMm(i)} mm)` }); return; }
  }
  // Marlin is never really homed (no G28), so it has no soft limits of its own: always stop
  // on an endstop switch. M400 lets the move finish before M121 turns checking off again.
  const feed = axis === "Z" ? cfg.jog_f_z : cfg.jog_f_xy;
  gcode("M120", "G91", `G1 ${axis}${d} F${feed}`, "G90", "M400", "M121", "M114");
}));

// The bridge homes (G28 stops Marlin): fast to the switch, back off, slow in, park.
function home(type) {
  const why = blocked();
  if (why) { addLine({ dir: "rx", text: why }); return; }
  send({ t: type });
}
$("#home").addEventListener("click", () => home("home_xy"));
$("#home-z").addEventListener("click", () => home("home_z"));
$("#motors-off").addEventListener("click", () => gcode("M84"));
$("#pos-refresh").addEventListener("click", () => gcode("M114"));

// ---------- Presets: little XY frames, a dot where the head goes ----------
const frames = $("#frames");
let presets = [];
let presetKey = "";
// Frames at edge / 50 / 100-edge % of each axis (setting preset_edge). Rebuilt when it changes.
function buildPresets() {
  const e = cfg.preset_edge;
  if (presetKey === String(e)) return;
  presetKey = String(e);
  const pct = [...new Set([e, 50, 100 - e])];
  frames.style.gridTemplateColumns = `repeat(${pct.length}, 1fr)`;
  frames.textContent = "";
  presets = [];
  for (const py of [...pct].reverse()) {   // top row = far Y
    for (const px of pct) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "frame";
      b.title = `Go to X ${px}% Y ${py}%`;
      b.innerHTML = `<span class="dot-at" style="left:${px}%;bottom:${py}%"></span><span class="lbl">${px}·${py}</span>`;
      b.addEventListener("click", () => goPreset(px, py));
      frames.appendChild(b);
      presets.push({ b, px, py });
    }
  }
}
buildPresets();

function goPreset(px, py) {
  if (!homed(0) || !homed(1)) { addLine({ dir: "rx", text: "Error: home X Y first" }); return; }
  // Machine position (0 = switch) to Marlin's logical one: logical = zero + dir * machine.
  const zero = state.zero || [0, 0, 0];
  const x = zero[0] + dirOf(0) * (maxMm(0) * px) / 100;
  const y = zero[1] + dirOf(1) * (maxMm(1) * py) / 100;
  gcode("M120", "G90", `G1 X${x.toFixed(2)} Y${y.toFixed(2)} F${cfg.preset_f}`, "M400", "M121", "M114");
}

function updatePresets() {
  const s = state;
  const ok = homed(0) && homed(1) && s.link === "up" && !s.stopped && !s.halted && !s.homing;
  const m = s.mpos || [];
  for (const p of presets) {
    p.b.disabled = !ok;
    const here = homed(0) && homed(1) && m[0] != null && m[1] != null &&
      Math.abs(m[0] - (maxMm(0) * p.px) / 100) < 0.5 && Math.abs(m[1] - (maxMm(1) * p.py) / 100) < 0.5;
    p.b.classList.toggle("here", here);
  }
  $("#preset-hint").textContent = homed(0) && homed(1)
    ? (m[0] != null ? `head at X ${m[0].toFixed(1)}  Y ${m[1].toFixed(1)} mm from the switches` : "")
    : "Home X Y first";
  $("#preset-range").textContent = `X Y, % of 0..${maxMm(0)} × 0..${maxMm(1)} mm`;
  updateLive();
}

// ---------- Live position: the head in a frame like the presets ----------
// Real position: the last M114 (sent after every jog, preset and home), as the machine position
// (0 = switch): dir * (logical - zero). Before the first M114, the bridge's tracked one.
function realPos(i) {
  if (!homed(i)) return null;
  const p = state.pos, zero = state.zero || [0, 0, 0];
  if (p && p[i] != null) return dirOf(i) * (p[i] - zero[i]);
  return state.mpos ? state.mpos[i] : null;
}
const clampPct = (v, max) => Math.min(100, Math.max(0, (v / max) * 100));
function updateLive() {
  const x = realPos(0), y = realPos(1), z = realPos(2);
  const xy = x != null && y != null;
  $("#live").classList.toggle("off", !xy);
  $("#live-xy").style.aspectRatio = `${maxMm(0)} / ${maxMm(1)}`;
  const dot = $("#live-dot");
  dot.classList.toggle("hidden", !xy);
  if (xy) {
    dot.style.left = clampPct(x, maxMm(0)) + "%";
    dot.style.bottom = clampPct(y, maxMm(1)) + "%";
  }
  $("#live-lbl").textContent = xy ? `X ${x.toFixed(1)} · Y ${y.toFixed(1)}` : "Home X Y to see the head";
  const zm = $("#live-zmark");
  zm.classList.toggle("hidden", z == null);
  if (z != null) zm.style.bottom = clampPct(z, maxMm(2)) + "%";
  $("#live-z").title = z == null ? "Z: home Z to see it" : `Z ${z.toFixed(1)} mm from the switch`;
}

// ---------- STOP: tap, then tap again within 3 s. After it: RESTART ----------
// STOP is a soft stop (M410, fan off, motors off): Marlin keeps running.
const stopBtn = $("#stop");
const stopLabel = $("#stop-label");
const stopIcon = $("#stop-icon");
let armTimer = 0;
const stopMode = () => (state.halted ? "halted" : state.stopped ? "restart" : "stop");

function showStopMode() {
  const mode = stopMode();
  if (mode !== "stop") disarm();
  if (stopBtn.classList.contains("arm")) return;
  stopBtn.classList.toggle("restart", mode === "restart");
  stopBtn.classList.toggle("halted", mode === "halted");
  stopLabel.textContent = mode === "halted" ? "HALTED" : mode === "restart" ? "RESTART" : "STOP";
  stopIcon.setAttribute("href", mode === "halted" ? "#i-alert" : mode === "restart" ? "#i-play" : "#i-stop");
  stopBtn.title = mode === "halted" ? "Marlin called kill(): power-cycle the printer"
    : mode === "restart" ? "Allow commands again (home before moving)"
    : "Stop: abort moves and homing, fan off, motors off";
}

stopBtn.addEventListener("click", () => {
  const mode = stopMode();
  if (mode === "halted") {
    addLine({ dir: "rx", text: "Error: Marlin halted (kill). Only a printer power-cycle or reset clears it" });
    return;
  }
  if (mode === "restart") {
    if (send({ t: "restart" })) addLine({ dir: "rx", text: "RESTART: commands allowed again, home before moving" });
    return;
  }
  if (!stopBtn.classList.contains("arm")) {
    stopBtn.classList.add("arm");
    stopLabel.textContent = "CONFIRM STOP";
    armTimer = setTimeout(() => { disarm(); showStopMode(); }, 3000);
    return;
  }
  disarm();
  showStopMode();
  if (send({ t: "stop" })) addLine({ dir: "rx", text: "STOP sent: moves aborted after the current command, fan off, motors off" });
});
function disarm() {
  clearTimeout(armTimer);
  stopBtn.classList.remove("arm");
}

// ---------- Setting tweaking: an iOS-style sheet, values kept by the bridge (NVS) ----------
// type: "num" (field + stepper) or "dir" (segmented control). The range, default and step
// come from the bridge ("d" in its settings message).
const SETTING_GROUPS = [
  { title: "Limits", foot: "A homed axis stays within 0..max from its switch. The bridge refuses any move outside, for every client.",
    rows: [["max_x", "Max X from home", "mm"], ["max_y", "Max Y from home", "mm"], ["max_z", "Max Z from home", "mm"]] },
  { title: "Homing", foot: "Z: try the direction on the loose motor first. Wrong way = it never meets the switch.",
    rows: [["park", "Park after home", "mm"], ["backoff", "Back off before slow pass", "mm"],
      ["xy_fast_f", "X Y seek speed", "mm/min"], ["xy_slow_f", "X Y slow pass", "mm/min"],
      ["z_dir", "Z switch is toward", "", "dir"], ["z_fast_step", "Z seek step", "mm"],
      ["z_slow_step", "Z fine step", "mm"], ["z_fast_f", "Z seek speed", "mm/min"], ["z_slow_f", "Z fine speed", "mm/min"]] },
  { title: "Moves", foot: "Presets sit at edge, 50 and 100 − edge % of each axis.",
    rows: [["jog_f_xy", "Jog speed X Y", "mm/min"], ["jog_f_z", "Jog speed Z", "mm/min"],
      ["preset_f", "Preset speed", "mm/min"], ["preset_edge", "Preset edge", "%"]] },
];
const sheet = $("#settings");
const sheetBody = $("#settings-body");
const inputs = {};       // key -> input, or the segmented control (with .value)
let sheetMsg = null;
let saving = false;

const decimals = (st) => (String(st).split(".")[1] || "").length;
const fmt = (k, v) => Number(v).toFixed(decimals((cfgDesc[k] || [0, 0, 0, 1])[3]));

function buildSheet() {
  sheetBody.textContent = "";
  for (const g of SETTING_GROUPS) {
    const t = document.createElement("div");
    t.className = "group-title"; t.textContent = g.title;
    const box = document.createElement("div");
    box.className = "group";
    for (const [key, label, unit, type] of g.rows) {
      const row = document.createElement("div");
      row.className = "srow";
      const id = "set-" + key;
      row.innerHTML = `<span class="changed" title="not the default"></span><label for="${id}">${label}</label>`;
      if (type === "dir") {
        const seg = document.createElement("div");
        seg.className = "seg"; seg.id = id; seg.setAttribute("role", "group");
        seg.innerHTML = `<button type="button" data-v="-1">−Z</button><button type="button" data-v="1">+Z</button>`;
        Object.defineProperty(seg, "value", {
          get() { return seg.dataset.v; },
          set(v) { seg.dataset.v = String(v); seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === String(v)))); },
        });
        seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { seg.value = b.dataset.v; markChanged(key); }));
        row.appendChild(seg);
        inputs[key] = seg;
      } else {
        const val = document.createElement("span");
        val.className = "val";
        const inp = document.createElement("input");
        inp.id = id; inp.type = "number"; inp.inputMode = "decimal";
        inp.addEventListener("input", () => markChanged(key));
        inp.addEventListener("change", () => { clampInput(key); markChanged(key); });
        val.append(inp, unit);
        const stp = document.createElement("span");
        stp.className = "stepper";
        stp.innerHTML = `<button type="button" aria-label="less">−</button><button type="button" aria-label="more">+</button>`;
        const [minus, plus] = stp.querySelectorAll("button");
        minus.addEventListener("click", () => nudge(key, -1));
        plus.addEventListener("click", () => nudge(key, 1));
        row.append(val, stp);
        inputs[key] = inp;
      }
      box.appendChild(row);
    }
    const f = document.createElement("div");
    f.className = "group-foot"; f.textContent = g.foot;
    sheetBody.append(t, box, f);
  }
  const reset = document.createElement("div");
  reset.className = "group";
  reset.style.marginTop = "22px";
  reset.innerHTML = `<div class="srow danger"><button type="button" id="settings-reset">Reset to defaults</button></div>`;
  sheetMsg = document.createElement("div");
  sheetMsg.className = "sheet-msg"; sheetMsg.setAttribute("aria-live", "polite");
  sheetBody.append(reset);
  sheetBody.prepend(sheetMsg);  // on top: offline note, save errors
  $("#settings-reset").addEventListener("click", () => {
    for (const k in inputs) if (cfgDesc[k]) setInput(k, cfgDesc[k][0]);
    showSheetMsg("Defaults filled in. Save to keep them.", true);
  });
  applyRanges();
}

function applyRanges() {
  for (const k in inputs) {
    const d = cfgDesc[k];
    const inp = inputs[k];
    if (!d || inp.tagName !== "INPUT") continue;
    inp.min = d[1]; inp.max = d[2]; inp.step = d[3];
    inp.placeholder = fmt(k, d[0]);
    inp.title = `${d[1]}..${d[2]}, default ${d[0]}`;
  }
}
function setInput(k, v) {
  const inp = inputs[k];
  if (!inp) return;
  inp.value = inp.tagName === "INPUT" ? fmt(k, v) : String(v);
  markChanged(k);
}
function readInput(k) { return Number(inputs[k].value); }
function clampInput(k) {
  const d = cfgDesc[k];
  const inp = inputs[k];
  if (!d || inp.tagName !== "INPUT") return;
  let v = Number(inp.value);
  if (inp.value === "" || !isFinite(v)) v = cfg[k];
  inp.value = fmt(k, Math.min(d[2], Math.max(d[1], v)));
}
function nudge(k, sign) {
  const d = cfgDesc[k];
  if (!d) return;
  const v = (Number(inputs[k].value) || 0) + sign * d[3];
  setInput(k, Math.min(d[2], Math.max(d[1], Math.round(v / d[3]) * d[3])));
}
function markChanged(k) {
  const d = cfgDesc[k];
  const row = (inputs[k] || {}).closest && inputs[k].closest(".srow");
  if (row && d) row.classList.toggle("is-changed", Math.abs(readInput(k) - d[0]) > 1e-6);
}
function showSheetMsg(text, ok) {
  if (!sheetMsg) return;
  sheetMsg.textContent = text || "";
  sheetMsg.classList.toggle("ok", !!ok);
}

// Offline the sheet still opens (last values seen, or the defaults), but Save waits for the bridge.
function sheetOnline() {
  $("#settings-save").disabled = !cfgLive;
  if (!sheet.open || saving) return;
  showSheetMsg(!cfgLive
    ? "Bridge offline: showing the last values it sent (defaults if never connected). Save works once it is online."
    : state.homing ? "Homing is running: settings can be saved after it." : "");
}
function openSheet() {
  for (const k in inputs) setInput(k, cfg[k]);
  saving = false;
  if (typeof sheet.showModal === "function") sheet.showModal(); else sheet.setAttribute("open", "");
  sheetOnline();
}
function closeSheet() {
  saving = false;
  if (sheet.open) sheet.close();
}
$("#open-settings").addEventListener("click", openSheet);
$("#settings-cancel").addEventListener("click", closeSheet);
sheet.addEventListener("click", (e) => { if (e.target === sheet) closeSheet(); });  // backdrop
$("#settings-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const msg = { t: "settings_set" };
  for (const k in inputs) {
    clampInput(k);
    msg[k] = readInput(k);
  }
  if (state.homing) { showSheetMsg("Homing is running: wait for it, then save."); return; }
  if (send(msg)) { saving = true; showSheetMsg("Saving..."); }
});

// The bridge answers a save with a "settings" message (done) or an Error:bridge: line.
function sheetLine(m) {
  if (saving && m.dir === "rx" && /^Error:bridge: (setting|the slow|settings)/.test(m.text)) {
    saving = false;
    showSheetMsg(m.text.replace(/^Error:bridge: /, ""));
  }
}
function onSettings(m) {
  cfg = Object.assign({}, cfg, m.v || {});
  cfgDesc = m.d || cfgDesc;
  try { localStorage.setItem("m200.settings", JSON.stringify({ v: cfg, d: cfgDesc })); } catch {}
  applyRanges();
  if (saving) closeSheet();
  else if (sheet.open && !cfgLive) for (const k in inputs) setInput(k, cfg[k]);  // came online
  cfgLive = true;
  sheetOnline();
  buildPresets();
  updatePresets();
}

buildSheet();
sheetOnline();
updateJog();
updatePresets();
connect();
