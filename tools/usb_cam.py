"""Serve a USB camera on this PC as an MJPEG stream for the bridge's web page (Camera row).

The bridge never sees the camera: the page loads the picture straight from this PC.

    pip install opencv-python                (once)
    python tools/usb_cam.py --list           which cameras give a picture
    python tools/usb_cam.py                  camera 0 -> http://<this-pc>:8081/stream
    python tools/usb_cam.py --cam 1 --size 1280x720 --port 8082
    python tools/usb_cam.py --backend msmf --list      Media Foundation numbering

On this PC (DirectShow numbering): cam 0 = the built-in "HD Webcam", cam 1 = the
"USB Live camera" (640x480 at 30 fps; --size 1280x720 about 9 fps, 1920x1080 about 3 fps).
Close its maker's app (Angetube) first: it holds the camera. Plug it straight into the PC,
not a hub. The camera's frame rate is never set (that stalls the USB Live camera); --fps
paces the server.

Then in the page: Setting tweaking -> Camera -> Source "Stream", URL http://<this-pc>:8081/stream
(or Source "Snapshot", URL .../snapshot). Any number of viewers share one capture.
The camera's own settings (brightness, contrast, exposure, white balance, focus, zoom, ...:
what the Angetube app changes) are at /controls; the page's sliders button in the Camera row
uses it. They are saved in ~/.m200_usb_cam.json and applied again at every start.
Bed scan (tools/scan_bed.py, run inside this server so one program holds the camera): the
page's Scan button (Camera row) posts /scan/start {"bridge": <the page's address>, "token": ...}
and follows /scan/status (tiles fill in, then the collage); /scan/stop stops before the next
move. /scans lists the last scans, /scans/<name>/<tile|collage|contact>.jpg[?w=N] serves their
pictures (tiles turned like the collage). The head moves through the bridge with the page's
token, so the bridge's own login still decides.
Windows asks once to let Python through the firewall: allow "Private networks" so phones
on the same WiFi can see it.
"""

import argparse
import collections
import importlib
import contextlib
import io
import json
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import cv2
except ImportError:
    sys.exit("OpenCV missing: pip install opencv-python")

# The web page's Scan button runs tools/scan_bed.py's scan in this server (one program holds
# the camera). Without it the camera still works, the scan endpoints just say why not.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import scan_bed
    SCAN_IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001
    scan_bed, SCAN_IMPORT_ERROR = None, e

BOUNDARY = "frame"
SAVED = Path.home() / ".m200_usb_cam.json"  # camera settings, per backend:camera

BACKENDS = {"msmf": cv2.CAP_MSMF, "dshow": cv2.CAP_DSHOW, "any": cv2.CAP_ANY}


def open_camera(index, size=None, fps=None, backend="dshow"):
    """Open camera `index` with one backend; the numbers differ between backends, so there is
    no fallback (it could open another camera). Windows: "dshow" (DirectShow) by default: it
    can change all the camera's settings and gives HD sizes as MJPEG. "msmf" (Media
    Foundation) changes few settings. Returns the capture once it has delivered a frame."""
    backends = [BACKENDS[backend] if sys.platform == "win32" else cv2.CAP_ANY]
    for be in backends:
        cap = cv2.VideoCapture(index, be)
        if not cap.isOpened():
            continue
        if size:
            if be == cv2.CAP_DSHOW:  # uncompressed USB 2 video tops out at 640x480
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        if fps:
            cap.set(cv2.CAP_PROP_FPS, fps)
        for _ in range(30):  # some cameras need a few frames to start
            ok, _frame = cap.read()
            if ok:
                return cap
            time.sleep(0.1)
        cap.release()
    return None


# The settings the camera maker's app shows (UVC controls): key, label, OpenCV property, the
# property that switches it to automatic, and a range for controls whose driver accepts any
# number (exposure: log2 seconds, -13 = 1/8192 s .. -1 = 1/2 s).
CONTROLS = [
    ("brightness", "Brightness", "BRIGHTNESS", None, None),
    ("contrast", "Contrast", "CONTRAST", None, None),
    ("saturation", "Saturation", "SATURATION", None, None),
    ("hue", "Hue", "HUE", None, None),
    ("sharpness", "Sharpness", "SHARPNESS", None, None),
    ("gamma", "Gamma", "GAMMA", None, None),
    ("gain", "Gain", "GAIN", None, None),
    ("backlight", "Backlight comp.", "BACKLIGHT", None, None),
    ("exposure", "Exposure", "EXPOSURE", "AUTO_EXPOSURE", (-13, -1)),
    ("wb", "White balance", "WB_TEMPERATURE", "AUTO_WB", None),
    ("focus", "Focus", "FOCUS", "AUTOFOCUS", None),
    ("zoom", "Zoom", "ZOOM", None, None),
    ("pan", "Pan", "PAN", None, None),
    ("tilt", "Tilt", "TILT", None, None),
]
FAR = 100000  # "accepts anything" marker while searching a range


class Controls:
    """The camera's adjustable settings. The driver gives no ranges through OpenCV, so each
    one is found by trying values (DirectShow refuses the ones out of range) and the current
    value is put back. Changes are saved to SAVED and applied again at the next start."""

    def __init__(self, cap, lock, saved_key):
        self.cap, self.lock, self.saved_key = cap, lock, saved_key
        self.items = {}  # key -> dict(label, prop, auto_prop, min, max, value, auto)
        with lock:
            for key, label, prop, auto_prop, fallback in CONTROLS:
                item = self._probe(label, getattr(cv2, "CAP_PROP_" + prop),
                                   auto_prop and getattr(cv2, "CAP_PROP_" + auto_prop), fallback)
                if item:
                    self.items[key] = item
        store = self._load()
        mine = store.setdefault(saved_key, {})
        # an automatic mode the driver doesn't report (exposure) counts as on: a dark room needs it
        unknown = {k: {"auto": True} for k, it in self.items.items()
                   if it["auto_prop"] and it["auto"] is None}
        if "defaults" not in mine:  # the first start: the camera's own values
            mine["defaults"] = {k: {**v, **unknown.get(k, {})} for k, v in self.values().items()}
            self._save(store)
        self.defaults = mine["defaults"]
        self.apply(mine.get("values") or unknown, save=False)

    def _probe(self, label, prop, auto_prop, fallback):
        cap = self.cap
        v0 = cap.get(prop)
        if not cap.set(prop, v0):
            return None

        def edge(good, bad):
            while abs(bad - good) > 1:
                mid = (good + bad) // 2
                good, bad = (mid, bad) if cap.set(prop, mid) else (good, mid)
            return good

        start = int(round(v0))
        lo = -FAR if cap.set(prop, -FAR) else edge(start, -FAR)
        hi = FAR if cap.set(prop, FAR) else edge(start, FAR)
        if lo == -FAR or hi == FAR:
            if not fallback:
                return None
            lo, hi = fallback
        cap.set(prop, v0)
        if lo >= hi:  # nothing to choose (Media Foundation often answers like this)
            return None
        auto = None
        if auto_prop:
            a = cap.get(auto_prop)  # 1 automatic; 0 or 2 (DirectShow "manual" flag) not
            auto = True if a == 1 else False if a in (0, 2) else None
        return {"label": label, "prop": prop, "auto_prop": auto_prop, "min": lo, "max": hi,
                "value": min(hi, max(lo, int(round(v0)))), "auto": auto}

    def values(self):
        return {k: {"value": it["value"], **({"auto": bool(it["auto"])} if it["auto_prop"] else {})}
                for k, it in self.items.items()}

    def describe(self):
        return {"controls": [
            {"key": k, "label": it["label"], "min": it["min"], "max": it["max"],
             "value": it["value"], **({"auto": bool(it["auto"])} if it["auto_prop"] else {})}
            for k, it in self.items.items()]}

    def apply(self, changes, save=True):
        """changes: {key: {"value": n, "auto": bool}}; unknown keys are ignored."""
        with self.lock:
            for key, ch in changes.items():
                it = self.items.get(key)
                if not it or not isinstance(ch, dict):
                    continue
                if "value" in ch and isinstance(ch["value"], (int, float)):
                    v = min(it["max"], max(it["min"], int(round(ch["value"]))))
                    if self.cap.set(it["prop"], v):  # a value switches automatic off
                        it["value"] = v
                        if it["auto_prop"]:
                            it["auto"] = False
                if it["auto_prop"] and "auto" in ch:
                    on = bool(ch["auto"])
                    was_auto = it["auto"] is not False
                    if self.cap.set(it["auto_prop"], 1 if on else 0):
                        it["auto"] = on
                    if not on and was_auto and "value" not in ch:
                        # automatic -> manual: write nothing, so the camera stays where
                        # automatic left it (the focus it found). Writing the slider value here
                        # moved the lens. The value read back is shown, but DirectShow often
                        # reports the last value set by hand rather than the live one.
                        live = self.cap.get(it["prop"])
                        if it["min"] <= live <= it["max"]:
                            it["value"] = int(round(live))
                    elif not on:  # manual already: back to the slider's value
                        self.cap.set(it["prop"], it["value"])
        if save:
            store = self._load()
            store.setdefault(self.saved_key, {"defaults": self.defaults})["values"] = self.values()
            self._save(store)

    def reset(self):
        self.apply(self.defaults)

    @staticmethod
    def _load():
        try:
            return json.loads(SAVED.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _save(store):
        try:
            SAVED.write_text(json.dumps(store, indent=1), encoding="utf-8")
        except OSError as e:
            print(f"could not save the camera settings: {e}", flush=True)


def list_cameras(backend, max_index=6):
    found = False
    for i in range(max_index):
        cap = open_camera(i, backend=backend)
        if cap:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f"  --cam {i}: picture {w}x{h}")
            cap.release()
            found = True
    if not found:
        print("  no camera gives a picture (in use by another app? unplugged?)")


class Camera:
    """One capture thread; viewers wait for the next JPEG."""

    def __init__(self, cap, fps, quality, lock):
        self.cap = cap
        self.lock = lock  # shared with Controls: no setting changes in the middle of a read
        self.period = 1.0 / fps
        self.params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        self.jpeg = None
        self.frame = None  # the same picture uncompressed, for /frame (bed scan tiles)
        self.seq = 0
        self.cond = threading.Condition()
        self.failed = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        misses = 0
        while True:
            t0 = time.monotonic()
            with self.lock:
                ok, frame = self.cap.read()
            if not ok:
                misses += 1
                if misses == 50:
                    print("camera stopped giving pictures (unplugged? in use?)", flush=True)
                    self.failed = True
                time.sleep(0.1)
                continue
            if misses >= 50:
                print("camera back", flush=True)
            misses = 0
            self.failed = False
            ok, buf = cv2.imencode(".jpg", frame, self.params)
            if ok:
                with self.cond:
                    self.jpeg = buf.tobytes()
                    self.frame = frame
                    self.seq += 1
                    self.cond.notify_all()
            time.sleep(max(0.0, self.period - (time.monotonic() - t0)))

    def next(self, after, timeout=5.0):
        """The first JPEG newer than `after`: (seq, bytes), or (after, None) on timeout."""
        with self.cond:
            self.cond.wait_for(lambda: self.seq != after, timeout)
            return (self.seq, self.jpeg) if self.seq != after else (after, None)

    def raw_after(self, after, timeout=5.0):
        """The first uncompressed frame newer than `after`: (seq, frame) or (after, None)."""
        with self.cond:
            self.cond.wait_for(lambda: self.seq > after and self.frame is not None, timeout)
            return (self.seq, self.frame) if self.seq > after else (after, None)


class LogTee(io.TextIOBase):
    """stdout while a scan runs: still printed, and the last lines kept for the page."""

    def __init__(self, lines):
        self.lines, self.part = lines, ""

    def write(self, s):
        sys.__stdout__.write(s)
        self.part += s
        while "\n" in self.part:
            line, self.part = self.part.split("\n", 1)
            if line.strip():
                self.lines.append(line.strip())
        return len(s)

    def flush(self):
        sys.__stdout__.flush()


class ScanRunner:
    """A bed scan (tools/scan_bed.py) started from the web page: one at a time, in a thread, with
    the page following it through /scan/status. This server takes the pictures for it."""

    def __init__(self, port):
        self.port = port
        self.lock = threading.Lock()
        self.thread = None
        self.stop_flag = False
        self.state = "idle"  # idle, running, stopping, done, stopped, error
        self.msg, self.folder, self.meta, self.plan = "", None, None, []
        self.done, self.current, self.collage = [], None, False
        self.log = collections.deque(maxlen=12)

    # --- the page --------------------------------------------------------
    def start(self, bridge, token=None):
        """Returns None, or why not."""
        if not re.match(r"^https?://[\w.:\[\]-]+/?$", bridge or ""):
            return "bridge address missing"
        why = reload_scan_bed()
        if why:
            return why
        with self.lock:
            if self.thread and self.thread.is_alive():
                return "a scan is already running"
            argv = ["--bridge", bridge, "--camera", f"http://127.0.0.1:{self.port}"]
            a = scan_bed.build_parser().parse_args(argv + (["--token", token] if token else []))
            a.viewer = self._viewer
            self.stop_flag = False
            self.state, self.msg = "running", "starting: checking the printer, locking the camera..."
            self.folder, self.meta, self.plan = None, None, []
            self.done, self.current, self.collage = [], None, False
            self.log.clear()
            self.thread = threading.Thread(target=self._run, args=(a,), daemon=True)
            self.thread.start()
        return None

    def stop(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.stop_flag = True
                self.state, self.msg = "stopping", "stopping after this move..."

    def status(self):
        with self.lock:
            st = {"state": self.state, "msg": self.msg, "log": list(self.log)[-6:],
                  "name": self.folder.name if self.folder else None, "collage": self.collage,
                  "current": self.current, "done": list(self.done)}
            if self.meta:
                w, h = self.meta["image_size"]
                st["aspect"] = h / w if self.meta["rot"] in (90, 270) else w / h
                st["plan"] = [{"row": p["row"], "col": p["col"], "x": p["x"], "y": p["y"],
                               "file": tile_name(p)} for p in self.plan]
            return st

    # --- scan_bed's viewer hooks (called from the scan thread) -----------
    def _viewer(self, plan, meta, folder):
        with self.lock:
            self.plan, self.meta, self.folder = plan, meta, folder
            self.msg = f"{len(plan)} tiles"
        return self

    def _check_stop(self):
        if self.stop_flag:
            raise KeyboardInterrupt  # scan_bed stops there: the head stays, camera put back

    def going(self, p):
        self._check_stop()
        with self.lock:
            self.current = tile_name(p)
            self.msg = (f"tile {len(self.done) + 1}/{len(self.plan)}: moving to row {p['row']} "
                        f"col {p['col']} (X{p['x']:.1f} Y{p['y']:.1f})")

    def tile(self, p, jpg):
        with self.lock:
            name = tile_name(p)
            if name not in self.done:
                self.done.append(name)
            self.current = None
            self.msg = f"tile {len(self.done)}/{len(self.plan)} taken: row {p['row']} col {p['col']}"

    def pump(self):
        self._check_stop()

    def finish(self, img):
        with self.lock:
            self.collage = img is not None
            self.msg = "collage ready"

    def _run(self, a):
        err = None
        tee = LogTee(self.log)
        try:
            with contextlib.redirect_stdout(tee):
                scan_bed.mode_scan(a, scan_bed.Cam(a.camera))
        except SystemExit as e:  # scan_bed's "can't start" messages
            err = str(e.code)
        except Exception as e:  # noqa: BLE001 - shown in the page, never kills the server
            err = f"{type(e).__name__}: {e}"
        with self.lock:
            self.current = None
            if self.folder and (self.folder / "collage.jpg").exists() and not self.stop_flag \
                    and len(self.done) == len(self.plan):
                self.collage = True
                self.state = "done"
                self.msg = f"Done: {len(self.done)} tiles, collage below (tap it for full size)."
            elif self.stop_flag:
                self.state, self.msg = "stopped", "stopped: the head stays where it is"
            else:
                last = next((l for l in reversed(self.log) if "stopped" in l), None)
                self.state, self.msg = "error", err or last or "the scan ended early"
            print(f"scan {self.state}: {self.msg}", flush=True)


def reload_scan_bed():
    """Load scan_bed.py again before every scan, so a changed scan_bed.py (grid, settings,
    collage) counts without restarting this server. None, or why it can't be loaded (the
    last good version then stays)."""
    global scan_bed, SCAN_IMPORT_ERROR
    try:
        scan_bed = importlib.reload(scan_bed) if scan_bed else importlib.import_module("scan_bed")
        SCAN_IMPORT_ERROR = None
        return None
    except Exception as e:  # noqa: BLE001 - a typo in scan_bed.py must not stop the camera
        SCAN_IMPORT_ERROR = e
        return f"scan_bed.py could not be loaded: {type(e).__name__}: {e}"


def tile_name(p):
    return f"tile_r{p['row']:02d}_c{p['col']:02d}.jpg"


def list_scans(limit=12):
    """The last scans, newest first."""
    out = []
    if scan_bed is None or not scan_bed.SCANS.is_dir():
        return out
    for d in sorted((p for p in scan_bed.SCANS.iterdir() if (p / "tiles.json").exists()),
                    reverse=True)[:limit]:
        meta = scan_bed.load_json(d / "tiles.json")
        out.append({"name": d.name, "created": meta.get("created"),
                    "tiles": len(list(d.glob("tile_r*_c*.jpg"))), "plan": len(meta.get("plan", [])),
                    "collage": (d / "collage.jpg").exists(), "contact": (d / "contact.jpg").exists()})
    return out


SCAN_FILE = re.compile(r"^(tile_r\d+_c\d+|collage|contact)\.jpg$")


def scan_picture(name, file, width=0):
    """JPEG bytes of a scan's picture, or None. Tiles come turned like the collage (the scan's
    rot/flip); width > 0 scales down."""
    if scan_bed is None or not re.match(r"^[\w.-]+$", name) or not SCAN_FILE.match(file):
        return None
    folder = scan_bed.SCANS / name
    path = folder / file
    if not path.is_file():
        return None
    if not width and not file.startswith("tile_"):
        return path.read_bytes()
    img = cv2.imread(str(path))
    if img is None:
        return None
    if file.startswith("tile_"):
        meta = scan_bed.load_json(folder / "tiles.json")
        img = scan_bed.orient(img, meta.get("flip_x", False), meta.get("flip_y", False),
                              meta.get("rot", 0))
    if width and img.shape[1] > width:
        img = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    return buf.tobytes() if ok else None


def make_handler(camera, controls, scans):
    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.1: a client keeps its connection (the bed scan asks for a frame or the state
        # many times a second). Every answer has a Content-Length; /stream closes at its end.
        protocol_version = "HTTP/1.1"
        timeout = 60  # an idle kept connection is closed after this many s

        def log_message(self, fmt, *args):  # quiet: one line per viewer instead
            pass

        def _headers(self, ctype, length=None, code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if length is None:  # no length (the stream): this connection ends with it
                self.send_header("Connection", "close")
                self.close_connection = True
            self.send_header("Cache-Control", "no-store")
            # the bridge's page (another origin) reads /controls
            self.send_header("Access-Control-Allow-Origin", "*")
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self._headers("application/json", len(body), code)
            self.wfile.write(body)

        def do_OPTIONS(self):  # CORS preflight, if a page sends JSON with its content type
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            """/controls: {"key": {"value": n, "auto": bool}, ...} or {"reset": true}.
            /scan/start: {"bridge": "http://<bridge>", "token": "..."}; /scan/stop."""
            path = self.path.split("?", 1)[0]
            if path == "/scan/stop":
                scans.stop()
                self._json(scans.status())
                return
            if path not in ("/controls", "/scan/start"):
                self.send_error(404)
                return
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= 4096:
                self.send_error(400, "body missing or too long")
                return
            try:
                body = json.loads(self.rfile.read(n))
            except ValueError:
                self.send_error(400, "not JSON")
                return
            if not isinstance(body, dict):
                self.send_error(400, "expected an object")
                return
            if path == "/scan/start":
                why = scans.start(str(body.get("bridge") or ""), body.get("token") or None)
                self._json({"error": why} if why else scans.status(), 409 if why else 200)
                return
            if body.get("reset") is True:
                controls.reset()
            else:
                controls.apply(body)
            self._json(controls.describe())

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/controls":
                self._json(controls.describe())
            elif path == "/frame":
                # the next frame after ?after=<seq> (X-Frame-Seq says which), lossless: the bed
                # scan averages a few of them per tile, no JPEG blocks. fmt=raw: the pixels as
                # they are (X-Width / X-Height / X-Channels), no PNG to make; else PNG
                m = re.search(r"(?:^|&)after=(\d+)", query)
                seq, frame = camera.raw_after(int(m.group(1)) if m else -1)
                if frame is None:
                    self.send_error(503, "no picture from the camera")
                    return
                raw = re.search(r"(?:^|&)fmt=raw(?:&|$)", query) is not None
                if raw:
                    body = frame.tobytes()
                else:
                    ok, buf = cv2.imencode(".png", frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                    if not ok:
                        self.send_error(503, "no picture from the camera")
                        return
                    body = buf.tobytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream" if raw else "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Seq", str(seq))
                if raw:
                    self.send_header("X-Width", str(frame.shape[1]))
                    self.send_header("X-Height", str(frame.shape[0]))
                    self.send_header("X-Channels", str(frame.shape[2] if frame.ndim == 3 else 1))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/scan/status":
                self._json(scans.status())
            elif path == "/scans":
                self._json({"scans": list_scans()})
            elif path.startswith("/scans/") and path.count("/") == 3:
                _, _, name, file = path.split("/")
                m = re.search(r"(?:^|&)w=(\d+)", query)
                jpg = scan_picture(name, file, min(int(m.group(1)), 4000) if m else 0)
                if jpg is None:
                    self.send_error(404)
                    return
                self._headers("image/jpeg", len(jpg))
                self.wfile.write(jpg)
            elif path == "/stream":
                self._stream()
            elif path in ("/snapshot", "/capture"):
                _, jpeg = camera.next(-1)
                if jpeg is None:
                    self.send_error(503, "no picture from the camera")
                    return
                self._headers("image/jpeg", len(jpeg))
                self.wfile.write(jpeg)
            elif path == "/":
                body = (b"<!doctype html><title>USB camera</title>"
                        b"<p>Stream: <a href=/stream>/stream</a> - snapshot: <a href=/snapshot>/snapshot</a></p>"
                        b"<img src=/stream style=max-width:100%>")
                self._headers("text/html; charset=utf-8", len(body))
                self.wfile.write(body)
            else:
                self.send_error(404)

        def _stream(self):
            who = self.client_address[0]
            print(f"viewer {who} connected", flush=True)
            self._headers(f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            seq = -1
            try:
                while True:
                    seq, jpeg = camera.next(seq)
                    if jpeg is None:
                        continue  # no new picture yet: keep the connection, wait again
                    self.wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                                     f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            print(f"viewer {who} left", flush=True)

    return Handler


def lan_ip():
    """This PC's address on the LAN (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true", help="list the cameras that give a picture")
    ap.add_argument("--cam", type=int, default=0, help="camera index (see --list), default 0")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--host", default="0.0.0.0", help="127.0.0.1: this PC only")
    ap.add_argument("--size", help="e.g. 1280x720 (the camera picks the nearest it has)")
    ap.add_argument("--fps", type=float, default=15)
    ap.add_argument("--quality", type=int, default=80, help="JPEG quality 1..100")
    ap.add_argument("--backend", choices=sorted(BACKENDS), default="dshow",
                    help="Windows camera API: dshow (default) or msmf; --list with the same one")
    a = ap.parse_args()

    if a.list:
        print(f"cameras ({a.backend}):")
        list_cameras(a.backend)
        return
    size = tuple(int(v) for v in a.size.lower().split("x")) if a.size else None
    # The camera's own frame rate is left alone: the "USB Live camera" stops delivering frames
    # (MSMF E_PENDING) when CAP_PROP_FPS is set. --fps only paces the capture thread.
    cap = open_camera(a.cam, size, None, a.backend)
    if not cap:
        sys.exit(f"camera {a.cam} gives no picture. Try --list (another app may be using it)")
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    lock = threading.Lock()
    controls = Controls(cap, lock, f"{a.backend}:{a.cam}")
    camera = Camera(cap, a.fps, a.quality, lock)
    scans = ScanRunner(a.port)
    server = ThreadingHTTPServer((a.host, a.port), make_handler(camera, controls, scans))
    server.daemon_threads = True
    host = lan_ip() if a.host == "0.0.0.0" else a.host
    print(f"camera {a.cam}: {w}x{h} at up to {a.fps:g} fps")
    print(f"  stream:   http://{host}:{a.port}/stream")
    print(f"  snapshot: http://{host}:{a.port}/snapshot")
    print(f"  settings: {', '.join(it['label'] for it in controls.items.values()) or 'none'}"
          f" (the page's Camera row, sliders button; saved in {SAVED})")
    print(f"  bed scan: the page's Scan / Collages buttons in the Camera row"
          f"{'' if scan_bed else f' (off: scan_bed.py not loaded: {SCAN_IMPORT_ERROR})'}")
    print("Put the stream URL in the page: Setting tweaking -> Camera. Ctrl+C stops.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()


if __name__ == "__main__":
    main()
