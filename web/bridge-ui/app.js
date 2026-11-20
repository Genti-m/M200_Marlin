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
      authRequired = m.auth === "required";
      if (authRequired) login(false);
    }
  };
}

// ---------- Login (API token, set on the USB console with !token) ----------
// Viewing is open; commands need the token once one is set. STOP never needs it.
const TOKEN_KEY = "m200.token";
let authRequired = false;  // the bridge's hello said a token is set
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

// ---------- Camera: a picture from any HTTP camera on the network ----------
// Chosen in Setting tweaking and kept in this browser (localStorage), not on the bridge: each
// phone / PC picks its own. "stream": an MJPEG URL shown live (ESP32-CAM: http://<cam>:81/stream,
// mjpg-streamer: ?action=stream). "snap": a JPEG URL loaded again every `every` seconds.
// The viewing device's own camera can't be used: browsers allow that only on https.
const CAM_KEY = "m200.camera";
const CAM_DEFAULT = { mode: "off", url: "", every: 2, rot: 0, flip: false };
const CAM_MAX_H = 420;  // px, outside full screen
function loadCam() {
  try { return Object.assign({}, CAM_DEFAULT, JSON.parse(localStorage.getItem(CAM_KEY) || "{}")); }
  catch { return Object.assign({}, CAM_DEFAULT); }
}
let cam = loadCam();
const camCard = $("#camera"), camBox = $("#cam-box"), camImg = $("#cam-img");
let camPaused = false;
let camWant = false;      // a picture is being loaded on purpose: errors after camStop() don't count
let camTimer = 0;         // next snapshot, or the next retry
let camWatch = 0;         // stream: waits for the first frame (not every browser fires "load")
let camRatio = 4 / 3;     // the picture's width / height, known after its first frame

const camUrlOk = (u) => /^https?:\/\/\S+$/i.test(u || "");
const camOn = () => cam.mode !== "off" && camUrlOk(cam.url);

function camMessage(text, setup) {
  $("#cam-msg").hidden = !text;
  $("#cam-msg-text").textContent = text || "";
  $("#cam-setup").hidden = !setup;
}

function camStop() {
  camWant = false;
  clearTimeout(camTimer);
  clearInterval(camWatch);
  camImg.removeAttribute("src");  // ends a stream: many cameras allow one viewer at a time
}

function camStart() {
  camStop();
  const on = camOn();
  $(".cam-tools").hidden = !on;
  if (camCtlUrl && camCtlUrl !== camCtlFor()) camCtlShowButton("");  // another camera chosen
  if (scanUrl && scanUrl !== scanBase()) scanShowButtons("");
  camBox.classList.toggle("paused", camPaused);
  $("#cam-pause-icon").setAttribute("href", camPaused ? "#i-play" : "#i-pause");
  $("#cam-pause").title = camPaused ? "Resume the camera" : "Pause the camera (frees it for another viewer)";
  if (!on) {
    camImg.hidden = true;
    camMessage(cam.mode === "off" ? "No camera chosen." : "The camera URL must start with http:// or https://", true);
    $("#cam-status").textContent = "";
    fitCam();
    return;
  }
  if (camPaused || document.hidden) {
    $("#cam-status").textContent = "paused";
    if (camImg.hidden) camMessage("Paused.", false);
    return;
  }
  $("#cam-status").textContent = "connecting...";
  if (camImg.hidden) camMessage("Connecting to " + cam.url, false);
  camWant = true;
  camImg.src = cam.mode === "snap" ? camSnapUrl() : cam.url;
  if (cam.mode === "stream") camWatch = setInterval(() => { if (camImg.naturalWidth) camShown(); }, 500);
}
const camSnapUrl = () => cam.url + (cam.url.includes("?") ? "&" : "?") + "_t=" + Date.now();
// The next snapshot replaces the shown one once it has loaded: no blank frame in between.
function camNextSnap() { if (camWant) camImg.src = camSnapUrl(); }

function camShown() {
  if (!camWant) return;
  clearInterval(camWatch);
  if (camImg.naturalWidth && camImg.naturalHeight) camRatio = camImg.naturalWidth / camImg.naturalHeight;
  camImg.hidden = false;
  camMessage("");
  fitCam();
  if (camCtlFor() !== camCtlTried) { camCtlTried = camCtlFor(); camCtlLoad(); scanProbe(); }
  if (cam.mode === "snap") {
    $("#cam-status").textContent = `snapshot every ${cam.every} s`;
    clearTimeout(camTimer);
    camTimer = setTimeout(camNextSnap, cam.every * 1000);
  } else {
    $("#cam-status").textContent = "live";
  }
}
camImg.addEventListener("load", camShown);
camImg.addEventListener("error", () => {
  if (!camWant) return;
  camStop();
  camCtlTried = "";  // the server may come back as another program
  camImg.hidden = true;
  camMessage("Camera not reachable: " + cam.url, false);
  $("#cam-status").textContent = "offline, retrying";
  camTimer = setTimeout(camStart, Math.max(5, cam.every) * 1000);
  fitCam();
});

// Size the box from the picture's aspect and rotation: as wide as the card, at most CAM_MAX_H
// high (the whole screen in full screen). The picture is centered and turned with a transform.
function fitCam() {
  const rot = (((Number(cam.rot) || 0) % 360) + 360) % 360;
  const side = rot === 90 || rot === 270;
  const full = document.fullscreenElement === camBox;
  if (camImg.hidden && !full) { camBox.style.width = ""; camBox.style.height = ""; return; }
  const cs = getComputedStyle(camCard);
  const availW = full ? innerWidth : camCard.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const maxH = full ? innerHeight : Math.min(CAM_MAX_H, innerHeight * 0.6);
  const r = side ? 1 / camRatio : camRatio;  // shown width / height
  const h = Math.max(1, Math.min(availW / r, maxH));
  const w = h * r;
  if (!full) { camBox.style.width = w + "px"; camBox.style.height = h + "px"; }
  camImg.style.width = (side ? h : w) + "px";
  camImg.style.height = (side ? w : h) + "px";
  camImg.style.transform = `translate(-50%, -50%) rotate(${rot}deg) scaleX(${cam.flip ? -1 : 1})`;
}

$("#cam-pause").addEventListener("click", () => { camPaused = !camPaused; camStart(); });
$("#cam-reload").addEventListener("click", () => { camPaused = false; camStart(); });
$("#cam-full").addEventListener("click", () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else if (camBox.requestFullscreen) camBox.requestFullscreen().catch(() => {});
});
document.addEventListener("fullscreenchange", fitCam);
document.addEventListener("visibilitychange", camStart);  // a hidden tab doesn't hold the stream
if (window.ResizeObserver) new ResizeObserver(fitCam).observe(camCard);
else window.addEventListener("resize", fitCam);

// ---------- Camera settings: brightness, exposure, white balance, focus, zoom... ----------
// Only for a camera server with a /controls API (tools/usb_cam.py: the same settings as the
// camera maker's app). Asked straight from the camera's address, never through the bridge.
// Other cameras: no sliders button. Every viewer changes the same camera.
let camCtlUrl = "";       // the /controls that answered ("" = none: no button)
let camCtlTried = "";     // asked once per camera (and again after it was offline)
let camCtlBusy = false;   // a POST is on its way: changes made meanwhile wait in camCtlNext
let camCtlNext = null;
let camCtlTimer = 0;
const camCtlEls = {};     // key -> { row, range, out, auto }

const camCtlFor = () => { try { return camOn() ? new URL(cam.url).origin + "/controls" : ""; } catch { return ""; } };
const camCtlOpen = () => !$("#cam-ctl").hidden;
function camCtlMsg(t) { $("#cam-ctl-msg").textContent = t || ""; }

function camCtlShowButton(url) {
  camCtlUrl = url;
  $("#cam-adjust").hidden = !url;
  if (!url) { $("#cam-ctl").hidden = true; $("#cam-adjust").setAttribute("aria-expanded", "false"); }
}

// Ask the camera server for its settings; shows the button if it has them.
async function camCtlLoad() {
  const url = camCtlFor();
  if (!url) { camCtlShowButton(""); return; }
  try {
    const r = await fetch(url, { cache: "no-store" });
    const js = await r.json();
    if (url !== camCtlFor()) return;  // the camera was changed meanwhile
    if (!r.ok || !Array.isArray(js.controls) || !js.controls.length) throw new Error();
    camCtlShowButton(url);
    camCtlRender(js.controls);
    camCtlMsg("");
  } catch {
    if (url !== camCtlFor()) return;
    if (camCtlOpen()) camCtlMsg("Camera settings not reachable.");
    else camCtlShowButton("");
  }
}

function camCtlRender(list) {
  const rows = $("#cam-ctl-rows");
  const keys = list.map((c) => c.key).join();
  if (rows.dataset.keys !== keys) {  // build once per camera; afterwards only the values change
    rows.textContent = "";
    for (const k in camCtlEls) delete camCtlEls[k];
    for (const c of list) rows.appendChild(camCtlRow(c));
    rows.dataset.keys = keys;
  }
  for (const c of list) {
    const e = camCtlEls[c.key];
    if (!e || (camCtlNext && c.key in camCtlNext) || document.activeElement === e.range) continue;
    e.range.min = c.min; e.range.max = c.max; e.range.value = c.value;
    e.out.value = c.value;
    if (e.auto) e.auto.checked = !!c.auto;
    e.row.classList.toggle("is-auto", !!c.auto);
  }
}

function camCtlRow(c) {
  const row = document.createElement("div");
  row.className = "ctl";
  const id = "cam-ctl-" + c.key;
  const name = document.createElement("label");
  name.className = "name"; name.htmlFor = id; name.textContent = c.label; name.title = c.label;
  const range = document.createElement("input");
  range.type = "range"; range.id = id; range.step = 1;
  const out = document.createElement("output");
  out.htmlFor = id;
  row.append(name, range, out);
  const e = { row, range, out, auto: null };
  range.addEventListener("input", () => {
    out.value = range.value;
    if (e.auto) { e.auto.checked = false; row.classList.remove("is-auto"); }  // a value means manual
    camCtlSend({ [c.key]: { value: Number(range.value) } });
  });
  if ("auto" in c) {
    const lab = document.createElement("label");
    lab.className = "auto";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.addEventListener("change", () => {
      row.classList.toggle("is-auto", box.checked);
      camCtlSend({ [c.key]: { auto: box.checked } });
    });
    lab.append(box, "Auto");
    row.appendChild(lab);
    e.auto = box;
  } else {
    row.appendChild(document.createElement("span"));
  }
  camCtlEls[c.key] = e;
  return row;
}

// Changes go out at most one request at a time; slider moves in between are merged.
function camCtlSend(change) {
  camCtlNext = camCtlNext || {};
  for (const k in change) camCtlNext[k] = Object.assign({}, camCtlNext[k], change[k]);
  clearTimeout(camCtlTimer);
  camCtlTimer = setTimeout(camCtlFlush, 80);
}
async function camCtlFlush() {
  if (camCtlBusy || !camCtlNext || !camCtlUrl) return;
  const body = camCtlNext;
  camCtlNext = null;
  await camCtlPost(body);
  if (camCtlNext) camCtlFlush();
}
async function camCtlPost(body) {
  camCtlBusy = true;
  try {
    // text/plain keeps it a "simple" request: no CORS preflight to the camera server
    const r = await fetch(camCtlUrl, { method: "POST", body: JSON.stringify(body) });
    const js = await r.json();
    if (!r.ok || !Array.isArray(js.controls)) throw new Error();
    camCtlRender(js.controls);
    camCtlMsg("");
  } catch {
    camCtlMsg("Not saved: camera settings not reachable.");
  } finally {
    camCtlBusy = false;
  }
}

$("#cam-adjust").addEventListener("click", () => {
  const open = !camCtlOpen();
  $("#cam-ctl").hidden = !open;
  $("#cam-adjust").setAttribute("aria-expanded", String(open));
  if (open) camCtlLoad();  // fresh values: another viewer may have changed them
});
$("#cam-ctl-reset").addEventListener("click", () => {
  clearTimeout(camCtlTimer);
  camCtlNext = null;
  camCtlPost({ reset: true });
});

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

// Segmented control: opts = [[value, label], ...]; .value reads / sets the pressed one.
function makeSeg(id, opts, onChange) {
  const seg = document.createElement("div");
  seg.className = "seg"; seg.id = id; seg.setAttribute("role", "group");
  for (const [v, label] of opts) {
    const b = document.createElement("button");
    b.type = "button"; b.dataset.v = v; b.textContent = label;
    b.addEventListener("click", () => { seg.value = v; onChange(); });
    seg.appendChild(b);
  }
  Object.defineProperty(seg, "value", {
    get() { return seg.dataset.v; },
    set(v) { seg.dataset.v = String(v); seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === String(v)))); },
  });
  return seg;
}

// The Camera group: kept in this browser, saved with the sheet's Save (also offline).
const camInputs = {};
let camGroupTitle = null;
function srow(label, forId) {
  const row = document.createElement("div");
  row.className = "srow";
  const dot = document.createElement("span");
  dot.className = "changed";  // never shown: keeps the labels in line with the other groups
  const l = document.createElement("label");
  l.textContent = label;
  if (forId) l.htmlFor = forId;
  row.append(dot, l);
  return row;
}
function buildCamGroup() {
  camGroupTitle = document.createElement("div");
  camGroupTitle.className = "group-title"; camGroupTitle.textContent = "Camera";
  const box = document.createElement("div");
  box.className = "group";

  const rMode = srow("Source");
  camInputs.mode = makeSeg("cam-mode", [["off", "Off"], ["stream", "Stream"], ["snap", "Snapshot"]], camRowsState);
  rMode.appendChild(camInputs.mode);

  const rUrl = srow("URL", "cam-url");
  const url = document.createElement("input");
  url.id = "cam-url"; url.className = "url"; url.type = "url"; url.inputMode = "url";
  url.autocomplete = "off"; url.spellcheck = false; url.pattern = "https?://\\S+";
  url.placeholder = "http://camera.local:81/stream";
  rUrl.appendChild(url);
  camInputs.url = url;

  const rEvery = srow("Snapshot every", "cam-every");
  const val = document.createElement("span");
  val.className = "val";
  const every = document.createElement("input");
  every.id = "cam-every"; every.type = "number"; every.inputMode = "numeric";
  every.min = 1; every.max = 60; every.step = 1; every.placeholder = String(CAM_DEFAULT.every);
  val.append(every, "s");
  rEvery.appendChild(val);
  camInputs.every = every;

  const rRot = srow("Rotate");
  camInputs.rot = makeSeg("cam-rot", [["0", "0°"], ["90", "90°"], ["180", "180°"], ["270", "270°"]], () => {});
  rRot.appendChild(camInputs.rot);

  const rFlip = srow("Mirror");
  camInputs.flip = makeSeg("cam-flip", [["0", "Off"], ["1", "On"]], () => {});
  rFlip.appendChild(camInputs.flip);

  box.append(rMode, rUrl, rEvery, rRot, rFlip);
  camInputs.rows = { url: rUrl, every: rEvery, rot: rRot, flip: rFlip };
  const f = document.createElement("div");
  f.className = "group-foot";
  f.textContent = "Saved in this browser only: each phone / PC picks its own. Any camera that serves "
    + "a picture over http works: an ESP32-CAM stream is usually http://<camera>:81/stream, a "
    + "snapshot http://<camera>/capture. Many cameras allow one viewer at a time: pause frees it. "
    + "A USB camera served by tools/usb_cam.py also gets a sliders button for its own settings.";
  sheetBody.append(camGroupTitle, box, f);
}
function camRowsState() {
  const mode = camInputs.mode.value;
  for (const k of ["url", "rot", "flip"]) camInputs.rows[k].classList.toggle("off", mode === "off");
  camInputs.rows.every.classList.toggle("off", mode !== "snap");
}
function fillCamInputs() {
  camInputs.mode.value = cam.mode;
  camInputs.url.value = cam.url;
  camInputs.every.value = cam.every;
  camInputs.rot.value = String(cam.rot);
  camInputs.flip.value = cam.flip ? "1" : "0";
  camRowsState();
}
// The camera from the sheet, or a reason it can't be saved.
function readCamInputs() {
  const mode = camInputs.mode.value;
  const url = camInputs.url.value.trim();
  if (mode !== "off" && !camUrlOk(url)) return { why: "Camera URL must start with http:// or https://" };
  const every = Math.round(Number(camInputs.every.value));
  return {
    cam: { mode, url, every: every >= 1 && every <= 60 ? every : CAM_DEFAULT.every,
      rot: Number(camInputs.rot.value) || 0, flip: camInputs.flip.value === "1" },
  };
}
function saveCam(c) {
  const changed = JSON.stringify(c) !== JSON.stringify(cam);
  cam = c;
  try { localStorage.setItem(CAM_KEY, JSON.stringify(cam)); } catch {}
  if (changed) { camPaused = false; camStart(); }
}

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
        const seg = makeSeg(id, [["-1", "−Z"], ["1", "+Z"]], () => markChanged(key));
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
  buildCamGroup();
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
    showSheetMsg("Defaults filled in (the camera is kept). Save to keep them.", true);
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

// Offline the sheet still opens (last values seen, or the defaults). Save keeps the camera at
// once (this browser); the bridge's settings wait for the bridge.
function sheetOnline() {
  if (!sheet.open || saving) return;
  showSheetMsg(!cfgLive
    ? "Bridge offline: showing the last values it sent (defaults if never connected). The camera saves now, the rest once the bridge is online."
    : state.homing ? "Homing is running: settings can be saved after it." : "");
}
// Any bridge setting in the sheet differs from what the bridge has.
function bridgeChanged() {
  for (const k in inputs) if (Math.abs(readInput(k) - cfg[k]) > 1e-6) return true;
  return false;
}
function openSheet() {
  for (const k in inputs) setInput(k, cfg[k]);
  fillCamInputs();
  saving = false;
  if (typeof sheet.showModal === "function") sheet.showModal(); else sheet.setAttribute("open", "");
  sheetOnline();
}
$("#cam-setup").addEventListener("click", () => {
  openSheet();
  if (camGroupTitle) camGroupTitle.scrollIntoView({ block: "start" });
});
function closeSheet() {
  saving = false;
  if (sheet.open) sheet.close();
}
$("#open-settings").addEventListener("click", openSheet);
$("#settings-cancel").addEventListener("click", closeSheet);
sheet.addEventListener("click", (e) => { if (e.target === sheet) closeSheet(); });  // backdrop
$("#settings-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const c = readCamInputs();
  if (c.why) { showSheetMsg(c.why); camInputs.url.focus(); return; }
  saveCam(c.cam);
  const msg = { t: "settings_set" };
  for (const k in inputs) {
    clampInput(k);
    msg[k] = readInput(k);
  }
  if (!cfgLive) {
    if (bridgeChanged()) showSheetMsg("Camera saved. The other settings need the bridge online: Save again then.");
    else closeSheet();
    return;
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

// ---------- Bed scan: tiles in a grid, then one collage (tools/scan_bed.py) ----------
// Run by the camera server on the PC (tools/usb_cam.py), which moves the head through this
// bridge's REST API with the token this page has. Buttons only for a camera server that
// answers /scan/status. Pictures come straight from the PC, never through the bridge.
let scanUrl = "";        // camera server origin that can scan ("" = no buttons)
let scanTimer = 0;
let scanLast = null;     // last /scan/status
let scanGridFor = "";    // scan name the grid was built for
const scanSheet = $("#scan"), collSheet = $("#collages");
const scanBase = () => { try { return camOn() ? new URL(cam.url).origin : ""; } catch { return ""; } };
const scanRunning = (st) => !!st && (st.state === "running" || st.state === "stopping");
const scanPic = (name, file, w) =>
  `${scanUrl}/scans/${encodeURIComponent(name)}/${file}${w ? "?w=" + w : ""}`;

function scanShowButtons(url) {
  scanUrl = url;
  $("#cam-scan").hidden = $("#cam-collages").hidden = !url;
}

async function scanProbe() {
  const base = scanBase();
  let ok = false;
  try {
    const r = await fetch(base + "/scan/status", { cache: "no-store" });
    const js = await r.json();
    ok = r.ok && typeof js.state === "string";
    if (ok && base === scanBase() && scanRunning(js)) scanLast = js;
  } catch {}
  if (base !== scanBase()) return;  // the camera was changed meanwhile
  scanShowButtons(ok ? base : "");
}

function openDialog(d) {
  if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
}

function scanSetMsg(text, err) {
  const m = $("#scan-msg");
  m.textContent = text || "";
  m.classList.toggle("err", !!err);
}

// The start button: what it does now, and whether it can.
function scanButton(st) {
  const go = $("#scan-go");
  const run = scanRunning(st);
  go.textContent = run ? "Stop" : "Start";
  const s = state;
  const ready = homed(0) && homed(1) && s.link === "up" && !s.stopped && !s.halted && !s.homing;
  go.disabled = run ? st.state === "stopping" : !ready;
  return ready;
}

function scanRender(st) {
  scanLast = st;
  const ready = scanButton(st);
  if (st.state === "idle" && !st.plan) {
    scanSetMsg(ready
      ? "Photographs the whole bed in overlapping tiles (X/Y moves at the current Z), then " +
        "puts them together into one collage. Start begins at once."
      : "Home X and Y first (Home button), then Start.", !ready);
  } else {
    scanSetMsg(st.msg, st.state === "error");
  }
  const grid = $("#scan-grid");
  if (!st.plan || !st.name) {
    if (!scanRunning(st)) { grid.textContent = ""; scanGridFor = ""; }
  } else {
    const rows = Math.max(...st.plan.map((p) => p.row)) + 1;
    const cols = Math.max(...st.plan.map((p) => p.col)) + 1;
    if (scanGridFor !== st.name) {  // one cell per tile, laid out like the bed: +Y up
      grid.textContent = "";
      grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
      // the whole grid in view (about 60 % of the screen height), so it can be watched filling
      grid.style.maxWidth = `calc(${(cols * (st.aspect || 4 / 3)) / rows} * 60vh)`;
      st.plan.forEach((p, i) => {
        const c = document.createElement("div");
        c.className = "scan-cell";
        c.dataset.file = p.file;
        c.style.gridRow = String(rows - p.row);
        c.style.gridColumn = String(p.col + 1);
        c.style.aspectRatio = String(st.aspect || 4 / 3);
        c.title = `row ${p.row}, column ${p.col}: X ${p.x.toFixed(1)}, Y ${p.y.toFixed(1)} mm`;
        c.textContent = "#" + (i + 1);
        const lbl = document.createElement("span");
        lbl.className = "lbl";
        lbl.textContent = `r${p.row} c${p.col}`;
        c.appendChild(lbl);
        grid.appendChild(c);
      });
      scanGridFor = st.name;
    }
    const last = st.done[st.done.length - 1];
    for (const c of grid.children) {
      const f = c.dataset.file;
      if (st.done.includes(f) && !c.querySelector("img")) {
        const img = document.createElement("img");
        img.alt = "Tile " + f;
        img.src = scanPic(st.name, f, 360);
        c.prepend(img);
      }
      c.classList.toggle("moving", st.current === f);
      c.classList.toggle("last", f === last && scanRunning(st));
    }
  }
  const res = $("#scan-result");
  const showColl = !!(st.collage && st.name && st.state === "done");
  if (showColl && res.dataset.name !== st.name) {
    $("#scan-collage").src = scanPic(st.name, "collage.jpg", 1400);
    $("#scan-collage-link").href = scanPic(st.name, "collage.jpg");
    res.dataset.name = st.name;
  }
  res.hidden = !showColl;
  const log = $("#scan-log");
  log.textContent = "";
  for (const l of st.log || []) {
    const li = document.createElement("li");
    li.textContent = l;
    log.appendChild(li);
  }
}

async function scanPoll() {
  clearTimeout(scanTimer);
  if (!scanUrl || !scanSheet.open) return;
  try {
    const r = await fetch(scanUrl + "/scan/status", { cache: "no-store" });
    scanRender(await r.json());
  } catch {
    scanSetMsg("The camera server (PC) is not reachable.", true);
  }
  if (scanSheet.open) scanTimer = setTimeout(scanPoll, scanRunning(scanLast) ? 700 : 2000);
}

async function scanPost(path, body) {
  try {
    const r = await fetch(scanUrl + path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
    });
    const js = await r.json();
    if (!r.ok) { scanSetMsg(js.error || "Refused", true); return; }
    scanRender(js);
  } catch {
    scanSetMsg("The camera server (PC) is not reachable.", true);
  }
  scanPoll();
}

$("#scan-go").addEventListener("click", () => {
  if (scanRunning(scanLast)) { scanPost("/scan/stop"); return; }
  if (authRequired && !savedToken()) { login(false); if (!savedToken()) return; }
  $("#scan-result").hidden = true;
  $("#scan-grid").textContent = "";  // the last scan's tiles go at once
  scanGridFor = "";
  // the PC moves the head through this bridge (the page's own address) with this page's token
  scanPost("/scan/start", { bridge: location.origin, token: savedToken() });
});
$("#cam-scan").addEventListener("click", () => {
  openDialog(scanSheet);
  if (scanLast) scanRender(scanLast);
  else scanButton(null);
  scanPoll();
});
$("#scan-close").addEventListener("click", () => scanSheet.close());
scanSheet.addEventListener("close", () => clearTimeout(scanTimer));

// The last scans: a collage thumbnail each, open it full size, or its tile grid.
function scanDate(name) {
  const m = /^(\d{4})-(\d\d)-(\d\d)_(\d\d)(\d\d)$/.exec(name);
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : name;
}

async function collagesLoad() {
  const list = $("#collage-list"), msg = $("#collages-msg");
  msg.textContent = "Loading...";
  msg.classList.remove("err");
  try {
    const r = await fetch(scanUrl + "/scans", { cache: "no-store" });
    const scans = (await r.json()).scans || [];
    list.textContent = "";
    msg.textContent = scans.length ? "" : "No scans yet: use the Scan button.";
    for (const s of scans) {
      const item = document.createElement("div");
      item.className = "collage-item";
      const thumb = document.createElement(s.collage ? "a" : "div");
      thumb.className = "thumb";
      if (s.collage) {
        thumb.href = scanPic(s.name, "collage.jpg");
        thumb.target = "_blank";
        thumb.rel = "noopener";
        thumb.title = "Open the collage full size";
        const img = document.createElement("img");
        img.alt = "Collage " + scanDate(s.name);
        img.loading = "lazy";
        img.src = scanPic(s.name, "collage.jpg", 420);
        thumb.appendChild(img);
      } else {
        const none = document.createElement("span");
        none.className = "none";
        none.textContent = "no collage";
        thumb.appendChild(none);
      }
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = scanDate(s.name);
      const sub = document.createElement("div");
      sub.className = "sub";
      sub.textContent = `${s.tiles}/${s.plan || s.tiles} tiles` + (s.tiles < s.plan ? " (stopped)" : "");
      const links = document.createElement("div");
      links.className = "links";
      const add = (text, file) => {
        const a = document.createElement("a");
        a.href = scanPic(s.name, file);
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = text;
        links.appendChild(a);
      };
      if (s.collage) add("Collage", "collage.jpg");
      if (s.contact) add("Tile grid", "contact.jpg");
      item.append(thumb, meta, sub, links);
      list.appendChild(item);
    }
  } catch {
    msg.textContent = "The camera server (PC) is not reachable.";
    msg.classList.add("err");
  }
}
$("#cam-collages").addEventListener("click", () => { openDialog(collSheet); collagesLoad(); });
$("#collages-close").addEventListener("click", () => collSheet.close());
$("#collages-reload").addEventListener("click", collagesLoad);

buildSheet();
sheetOnline();
updateJog();
updatePresets();
camStart();
connect();
