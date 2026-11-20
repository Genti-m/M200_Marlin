#!/usr/bin/env python3
"""PC mock of the M200 bridge: serves web/bridge-ui and fakes the WebSocket/REST API
(docs/protocol.md) with a simulated Marlin. Python 3 standard library only.

    python tools/mock_bridge.py            -> http://localhost:8080
    python tools/mock_bridge.py --port 9000

Mock-only console commands: "!kill" (fake MINTEMP kill), "!unplug bed" / "!unplug nozzle"
(toggle an open sensor: reads -14 C, the bridge guard then refuses heating and Z homing).
"""
import argparse
import base64
import hashlib
import json
import queue
import random
import re
import socket
import struct
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent.parent / "web" / "bridge-ui"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
LINE_MAX = 96
# Settings, like SETTINGS[] in printer_state.c: key -> [default, min, max, step]
SETTINGS_DESC = {
    "max_x": [170, 10, 400, 1], "max_y": [170, 10, 400, 1], "max_z": [170, 10, 400, 1],
    "park": [10, 1, 50, 1], "backoff": [1, 0.2, 5, 0.1],
    "xy_fast_f": [3000, 100, 6000, 100], "xy_slow_f": [300, 30, 1500, 10],
    "z_dir": [-1, -1, 1, 2], "z_fast_step": [0.5, 0.1, 5, 0.1], "z_slow_step": [0.1, 0.01, 1, 0.01],
    "z_fast_f": [600, 30, 1500, 10], "z_slow_f": [120, 10, 600, 10],
    "jog_f_xy": [3000, 100, 6000, 100], "jog_f_z": [600, 30, 1500, 10],
    "preset_f": [3000, 100, 6000, 100], "preset_edge": [10, 0, 40, 1],
}
settings = {k: d[0] for k, d in SETTINGS_DESC.items()}  # the mock keeps them in memory only


def parse_gcode(line):
    """(word, {letter: value}) read like gcode_guard.c in the firmware: no spaces needed
    ("N12G28X", "M104S200", "G1X10Y200"), the first of each letter wins, ";" comments and
    "*" checksums cut off, "nan" is a number. "G28.1" is not G28. Spaces between letter and
    number are skipped like Marlin does ("M 104 S200" is M104)."""
    cmd = re.sub(r";.*$|\*.*$", "", line).strip().upper()
    cmd = re.sub(r"^N\d+ *", "", cmd)
    m = re.match(r"([GM]) *(\d+)(?![\d.])", cmd)
    if m:
        word, rest = m.group(1) + m.group(2), cmd[m.end():]
    else:
        word = cmd.split(" ")[0] if cmd else ""
        rest = cmd[len(word):]
    args = {}
    for k, v in re.findall(r"(?<![A-Z])([A-Z])\s*(-?(?:\d+\.?\d*|\.\d+)|NAN|INF)", rest):
        args.setdefault(k, float(v))
    return word, args


def settings_check(v):
    """Like settings_check() in printer_state.c: why not, or None."""
    for k, (_, lo, hi, _) in SETTINGS_DESC.items():
        if not lo <= v[k] <= hi:
            return f"Error:bridge: setting {k} must be {lo:g}..{hi:g}"
    if v["z_dir"] not in (-1, 1):
        return "Error:bridge: setting z_dir must be -1 or 1"
    if any(v["park"] >= v[k] for k in ("max_x", "max_y", "max_z")):
        return "Error:bridge: setting park must be below every axis max"
    if v["backoff"] >= v["park"]:
        return "Error:bridge: setting backoff must be below park"
    if v["xy_slow_f"] > v["xy_fast_f"] or v["z_slow_f"] > v["z_fast_f"] or v["z_slow_step"] > v["z_fast_step"]:
        return "Error:bridge: the slow pass can't be faster or coarser than the seek"
    return None


def axis_max(i):
    return settings[("max_x", "max_y", "max_z")[i]]


# ---------------------------------------------------------------- clients
class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = set()
        self.history = []

    def add(self, c):
        with self.lock:
            self.clients.add(c)

    def remove(self, c):
        with self.lock:
            self.clients.discard(c)

    def broadcast(self, msg):
        if msg.get("t") == "line":
            self.history = (self.history + [msg])[-100:]
        data = json.dumps(msg)
        with self.lock:
            dead = [c for c in self.clients if not c.send_text(data)]
            for c in dead:
                self.clients.discard(c)


hub = Hub()


# ---------------------------------------------------------------- fake Marlin
class FakeMarlin:
    def __init__(self):
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.nozzle = [22.0, 0.0]
        self.bed = [21.0, 0.0]
        self.pos = [0.0, 0.0, 0.0]
        self.relative = False
        self.sub_relative = False  # G91 as of the accepted lines (the limit check uses it)
        self.homed = [False, False, False]  # X Y Z, homed by the bridge (no G28)
        self.homing = False
        self.busy_until = 0.0
        self.halted = False
        self.stopped = False  # soft STOP, until "restart"
        self.unplugged = {"nozzle": False, "bed": False}
        self.temp_log = []  # (time, n_act, n_tgt, b_act, b_tgt), 10 min
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._physics, daemon=True).start()

    # bridge side ----------------------------------------------------
    def send(self, line, src="web"):
        line = line.strip()
        if not line or len(line) > LINE_MAX:
            return False
        hub.broadcast({"t": "line", "dir": "tx", "src": src, "text": line})
        self.q.put(line)
        return True

    def _flush(self):
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def soft_stop(self, src="web"):
        # like printer_state_soft_stop(): M410 ahead of the queue, then heaters/fan/motors off
        self._flush()
        with self.lock:
            self.stopped, self.homed, self.relative, self.sub_relative = True, [False] * 3, False, False
        hub.broadcast({"t": "line", "dir": "tx", "src": src, "text": "M410"})
        for line in ("M104 S0", "M140 S0", "M107", "M84"):
            self.send(line, src)
        self._push_state()

    def restart(self):
        with self.lock:
            self.stopped = False
        self._push_state()

    def home(self, axes):
        # like printer_state_home_xy/z(): switch found after a few seconds, park at 10
        if self.stopped or self.halted or self.homing:
            self._rx("Error:bridge: homing not possible now (running, stopped, halted or no printer)")
            return
        idx = [i for i, ax in enumerate("XYZ") if ax in axes]
        with self.lock:
            self.homing = True
            for i in idx:
                self.homed[i] = False
        self._rx(f"echo:bridge: homing {' '.join(axes)}...")
        self._push_state()

        def run():
            time.sleep(3)
            with self.lock:
                for i in idx:
                    self.pos[i], self.homed[i] = float(settings["park"]), True
                self.homing = False
            self._rx(f"echo:bridge: {' '.join(axes)} homed, parked off the switch")
            self._push_state()
        threading.Thread(target=run, daemon=True).start()

    def kill(self, reason="Error:MINTEMP triggered, system stopped! Heater_ID: bed"):
        # mock only: type "!kill" in the console to see a real Marlin kill() (needs a reset)
        self._flush()
        threading.Thread(target=self._kill, args=(reason,), daemon=True).start()

    def state(self):
        with self.lock:
            return {
                "link": "no_reply" if self.halted else "up",
                "nozzle": [round(self.nozzle[0], 1), self.nozzle[1]],
                "bed": [round(self.bed[0], 1), self.bed[1]],
                "busy": time.time() < self.busy_until,
                "homing": self.homing,
                "homed": list(self.homed),
                "mpos": [round(v, 2) if h else None for v, h in zip(self.pos, self.homed)],
                "zero": [0.0, 0.0, 0.0],  # the mock doesn't model G92 or z_dir
                "dir": [1, 1, 1],
                "max": [axis_max(i) for i in range(3)],
                "stopped": self.stopped,
                "halted": self.halted,
                "unplugged": dict(self.unplugged),
                "pos": [round(v, 2) for v in self.pos],
                "rssi": random.randint(-58, -50),
                "queue": self.q.qsize(),
            }

    # printer side ---------------------------------------------------
    def _rx(self, text):
        hub.broadcast({"t": "line", "dir": "rx", "text": text})

    def _push_state(self):
        hub.broadcast({"t": "state", **self.state()})

    def _temps(self):
        n, b = self.nozzle, self.bed
        return f"T:{n[0]:.2f} /{n[1]:.2f} B:{b[0]:.2f} /{b[1]:.2f} @:0 B@:0"

    def _busy(self, seconds):
        end = time.time() + seconds
        self.busy_until = end + 0.5
        while time.time() < end:
            time.sleep(min(2.0, end - time.time()))
            if time.time() < end:
                self._rx("echo:busy: processing")

    def _kill(self, reason):
        while time.time() < self.busy_until:
            time.sleep(0.1)
        with self.lock:
            self.halted, self.homed = True, [False] * 3
            self.nozzle[1] = self.bed[1] = 0.0
        self._rx(reason)
        self._rx("Error:Printer halted. kill() called!")
        self._push_state()
        time.sleep(8)  # someone power-cycles the printer
        with self.lock:
            self.halted = self.stopped = False
        self._rx("start")
        self._rx("echo:Marlin bugfix-2.1.x")
        self._push_state()

    def _worker(self):
        while True:
            line = self.q.get()
            if self.halted:
                continue
            word, args = parse_gcode(line)
            reply = self._run(word, args)
            if reply:
                self._rx(reply)
            self._push_state()

    def _run(self, word, a):
        time.sleep(0.02)
        if word == "G92":
            with self.lock:
                for i, ax in enumerate("XYZ"):
                    if ax in a:
                        self.pos[i] = a[ax]
        elif word in ("G0", "G1"):
            with self.lock:
                for i, ax in enumerate("XYZ"):
                    if ax in a:
                        v = self.pos[i] + a[ax] if self.relative else a[ax]
                        self.pos[i] = max(0.0, min((200, 200, 180)[i], v))
            time.sleep(0.15)
        elif word == "G90":
            self.relative = False
        elif word == "G91":
            self.relative = True
        elif word in ("M84", "M18"):
            with self.lock:
                self.homed = [False] * 3
        elif word in ("M104", "M109"):
            self.nozzle[1] = a.get("S", 0.0)
            if word == "M109":
                self._wait(self.nozzle)
        elif word in ("M140", "M190"):
            self.bed[1] = a.get("S", 0.0)
            if word == "M190":
                self._wait(self.bed)
        elif word == "M105":
            return "ok " + self._temps()
        elif word == "M114":
            x, y, z = self.pos
            self._rx(f"X:{x:.2f} Y:{y:.2f} Z:{z:.2f} E:0.00 Count X:{int(x*80)} Y:{int(y*80)} Z:{int(z*400)}")
        elif word == "M115":
            self._rx("FIRMWARE_NAME:Marlin bugfix-2.1.x (Aug 13 2025) SOURCE_CODE_URL:github.com/MarlinFirmware/Marlin "
                     "PROTOCOL_VERSION:1.0 MACHINE_TYPE:Zortrax M200 EXTRUDER_COUNT:1")
            self._rx("Cap:AUTOREPORT_TEMP:1")
            self._rx("Cap:EEPROM:0")
            self._rx("Cap:EMERGENCY_PARSER:0")
        elif word == "M503":
            for l in ("echo:; Steps per unit:", "echo: M92 X80.00 Y80.00 Z400.00 E95.00",
                      "echo:; Max feedrates (units/s):", "echo: M203 X200.00 Y200.00 Z12.00 E25.00"):
                self._rx(l)
        elif word in ("M155", "M106", "M107", "M110", "M117", "M400", "M220", "M221", "M120", "M121"):
            pass
        else:
            self._rx(f'echo:Unknown command: "{word}"')
        return "ok"

    def refusal(self, line):
        # same rules as printer_state_submit() (the guard) in the firmware
        if self.stopped:
            return "Error:bridge: stopped, press RESTART first"
        word, a = parse_gcode(line)
        target = a.get("S", a.get("R"))
        heat = target is not None and not target <= 0  # NaN counts as heat, like the firmware
        heat = heat or "I" in a  # material preset: wins over S in Marlin
        if word in ("M104", "M109", "M140", "M190") and heat:
            return "Error:bridge: laser build, no heaters: heating refused (S0 / off works)"
        if word == "M303":
            return "Error:bridge: laser build, no heaters: PID autotune refused"
        if word in ("G29", "G30", "M48"):
            return "Error:bridge: laser build, probing refused (its preheat stops Marlin)"
        if word == "G28":
            return "Error:bridge: laser build, G28 refused (its Z preheat stops Marlin). Use the Home button"
        if self.homing:
            return "Error:bridge: homing running, wait for it (STOP aborts it)"
        if word in ("G0", "G1", "G2", "G3"):
            for i, ax in enumerate("XYZ"):
                v = a.get(ax)
                if self.homed[i] and v is not None:
                    to = self.pos[i] + v if self.sub_relative else v
                    if not -0.001 <= to <= axis_max(i) + 0.001:
                        return f"Error:bridge: {ax} would go to {to:.2f} mm from home, limit 0..{axis_max(i):g}"
        return None

    def guarded_send(self, line, src="web"):
        why = self.refusal(line)
        if why:
            self._rx(why)
            return False
        # like track(): the mode as of the accepted lines, before they run
        word, _ = parse_gcode(line)
        if word in ("G90", "G91"):
            self.sub_relative = word == "G91"
        return self.send(line, src)

    def _wait(self, heater):
        while abs(heater[0] - heater[1]) > 1.5 and not self.halted:
            self._rx(" " + self._temps() + " W:?")
            time.sleep(1)

    def _physics(self):
        last_report = 0.0
        while True:
            time.sleep(0.25)
            with self.lock:
                for name, h, rate in (("nozzle", self.nozzle, 0.035), ("bed", self.bed, 0.012)):
                    if self.unplugged[name]:
                        h[0] = -14.0  # open sensor
                        if h[1] > 0 and not self.halted:  # Marlin MINTEMP: only with target > 0
                            self.halted = True
                            reason = f"Error:MINTEMP triggered, system stopped! Heater_ID: {'bed' if name == 'bed' else 'E0'}"
                            threading.Thread(target=self._kill, args=(reason,), daemon=True).start()
                        continue
                    goal = h[1] if h[1] > 0 else 21.0
                    h[0] += (goal - h[0]) * rate + random.uniform(-0.08, 0.08)
            if time.time() - last_report >= 2 and not self.halted:  # M155 S2
                last_report = time.time()
                with self.lock:
                    self.temp_log.append((last_report, round(self.nozzle[0], 1), self.nozzle[1],
                                          round(self.bed[0], 1), self.bed[1]))
                    del self.temp_log[:-300]
                self._rx(" " + self._temps())
                self._push_state()


marlin = FakeMarlin()


# ---------------------------------------------------------------- HTTP + WebSocket
class WsClient:
    def __init__(self, sock):
        self.sock = sock
        self.wlock = threading.Lock()

    def send_text(self, text):
        data = text.encode()
        n = len(data)
        head = bytes([0x81]) + (bytes([n]) if n < 126 else
                                bytes([126]) + struct.pack(">H", n) if n < 65536 else
                                bytes([127]) + struct.pack(">Q", n))
        try:
            with self.wlock:
                self.sock.sendall(head + data)
            return True
        except OSError:
            return False

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def frames(self):
        while True:
            b0, b1 = self._read(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else b"\0\0\0\0"
            data = bytes(c ^ mask[i % 4] for i, c in enumerate(self._read(n)))
            if op == 0x8:
                return
            if op == 0x9:
                with self.wlock:
                    self.sock.sendall(bytes([0x8A, len(data)]) + data)
            elif op == 0x1:
                yield data.decode(errors="replace")


def handle_ws_message(text):
    try:
        m = json.loads(text)
    except ValueError:
        return
    if m.get("t") == "gcode":
        for line in m.get("lines", [])[:20]:
            line = str(line)
            if re.match(r"^\s*M *112\b", line, re.I):
                marlin.soft_stop()
            elif re.match(r"^\s*!kill\b", line, re.I):
                marlin.kill()
            elif re.match(r"^\s*!unplug\s+(bed|nozzle)\b", line, re.I):
                name = line.split()[1].lower()
                marlin.unplugged[name] = not marlin.unplugged[name]
                marlin._push_state()
            else:
                marlin.guarded_send(line)
    elif m.get("t") == "stop":
        marlin.soft_stop()
    elif m.get("t") == "restart":
        marlin.restart()
    elif m.get("t") == "home_xy":
        marlin.home("XY")
    elif m.get("t") == "home_z":
        marlin.home("Z")
    elif m.get("t") == "settings_set":
        apply_settings(m)


def settings_msg():
    return {"t": "settings", "v": dict(settings), "d": SETTINGS_DESC}


def apply_settings(m):
    """Like apply_settings() + printer_state_set_settings(): why not, or None."""
    v = dict(settings)
    for k in SETTINGS_DESC:
        if k in m:
            try:
                v[k] = float(m[k])
            except (TypeError, ValueError):
                why = "Error:bridge: settings: not a number"
                marlin._rx(why)
                return why
    why = settings_check(v) or (marlin.homing and "Error:bridge: settings can't change while homing")
    if why:
        marlin._rx(why)
        return why
    if v != settings:
        settings.update(v)
        marlin._rx("echo:bridge: settings saved")
    hub.broadcast(settings_msg())
    marlin._push_state()
    return None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(UI_DIR), **kw)

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
            return self._websocket()
        if self.path == "/api/state":
            return self._json(marlin.state())
        if self.path == "/api/settings":
            return self._json({k: v for k, v in settings_msg().items() if k != "t"})
        return super().do_GET()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)).decode(errors="replace")
        if self.path == "/api/gcode":
            n = sum(marlin.guarded_send(l) for l in body.splitlines() if l.strip())
            return self._json({"queued": n})
        if self.path == "/api/settings":
            try:
                why = apply_settings(json.loads(body or "{}"))
            except ValueError:
                why = "Error:bridge: settings: not a number"
            return self._json({"ok": True} if not why else {"ok": False, "error": why})
        if self.path == "/api/stop":
            marlin.soft_stop(src="internal")
            return self._json({"ok": True})
        if self.path == "/api/restart":
            marlin.restart()
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)

    def _websocket(self):
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.send_response_only(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        super().end_headers()
        self.wfile.flush()
        self.close_connection = True
        client = WsClient(self.connection)
        now = time.time()
        with marlin.lock:
            temps = [[round(now - t, 1), na, nt, ba, bt] for t, na, nt, ba, bt in marlin.temp_log]
        client.send_text(json.dumps({"t": "hello", "name": "m200-mock", "fw": "mock", "auth": "open",
                                     "history": hub.history, "temps": temps}))
        client.send_text(json.dumps(settings_msg()))
        client.send_text(json.dumps({"t": "state", **marlin.state()}))
        hub.add(client)
        try:
            for text in client.frames():
                handle_ws_message(text)
        except (ConnectionError, OSError):
            pass
        finally:
            hub.remove(client)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to test from a phone on the same WiFi")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"mock bridge on http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}  (UI from {UI_DIR})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
