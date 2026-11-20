"""Photograph the bed in a grid of overlapping tiles and build one collage image.

The camera is served by tools/usb_cam.py on this PC, the head is moved through the bridge's
REST API (docs/protocol.md). Pictures go to scans/<date_time>/ in the repo root.

    python tools/usb_cam.py --cam 1 --size 1280x720      (in another window, first)
    python tools/scan_bed.py --check                     camera on the head? mm/px, orientation
    python tools/scan_bed.py --calibrate                 or: click a ruler's ends, type the mm
    python tools/scan_bed.py --dry-run                   print the grid and the moves only
    python tools/scan_bed.py                             scan the whole bed (limits from the bridge)
    python tools/scan_bed.py --area 20,20,120,100 --overlap 0.3
    python tools/scan_bed.py --show                      watch the tiles fill in, then the collage
    python tools/scan_bed.py --resume                    finish the last scan (skips saved tiles)
    python tools/scan_bed.py --collage-only scans/2026-11-19_1530 --blend feather
    python tools/scan_bed.py --lens-calibrate --board 9x6   then add --undistort to a scan
    python tools/scan_bed.py --offset-calibrate 85,85    camera offset from the tool (a mark
                                                         made there, then click it)
    python tools/scan_bed.py --collage-only <folder> --res 0.1 --crop-travel

Coordinates are mm from the home switches (the bridge's mpos, 0..max_x/max_y), the same
frame its guard checks. The head is never homed here: press Home XY in the page first.
Z is never moved. Ctrl+C stops after the current move: the head stays where it is.

Orientation: after --flip-x (mirror), --rot (clockwise, like the page's Rotate/Mirror) and
--flip-y, the photo's right must be +X and its top +Y. --check works this out by moving the
head 2 mm and measuring how the picture shifts, and saves it with mm/px in
scans/calibration.json, which later runs use unless given on the command line.

Collage: the exact camera-to-bed mapping (scale, any rotation angle, mirror) is measured from
the overlaps of neighbouring tiles (phase correlation of the strip both show; weak or
inconsistent pairs dropped), so a few degrees of camera tilt or a ruler off by some percent
don't break it. Saved in calibration.json for scans too plain to measure (--no-fit: skip).
Then the camera itself is fitted (--no-fit: not): a camera that doesn't look straight down
sees the far side smaller (keystone) and the lens bends the edges, so a scale + rotation only
fits the middle of each overlap. Hundreds of small patches across the overlaps are matched and
the lens, the tilt and every tile's real position solved together; it is kept in
calibration.json, and tiles are drawn through it.
Each pixel comes from the tile that shows it best, i.e. nearest its centre, but not where
that tile is blown out or shows the light's reflection when a neighbour saw the spot cleanly
(--no-glare: always the nearest). Things standing up from the bed (pliers, tape) look
different from each tile: each is taken whole from one tile where possible (--no-whole).
The tiles' brightness is evened out from their overlaps (--no-gain), the bright spot every
tile shares too (--no-flat), grey things are made grey and the brightest parts put just below
white (--no-wb). --blend multiband (default) blends the seams per frequency: light changes
smoothly, detail stays sharp; crop: no blending; feather: soft.
Written next to the tiles: collage.jpg; collage.json, where it lies on the bed (pixel (u, v)
centre = head mm x0 + u * mm_per_px, y1 - v * mm_per_px) and how well the pairs agree;
collage_seams.jpg, the seams coloured blue (tiles agree) to red (a hole or more off).
--offset-calibrate measures where the camera looks relative to the tool: the collage's mm are
then where the tool goes. --res sets the collage's mm/px, --crop-travel cuts it to 0..max.

Camera settings: auto exposure, white balance and focus are switched off for the scan (so all
tiles look the same): focus and white balance stay where automatic had them when the scan
starts (focus is searched only if the lens moves anyway), exposure is matched to the current
automatic brightness and then made darker (at most 2 steps) while whites are blown out
(--meter mean: not), unless --focus / --wb / --exposure give values. They are put back at
the end (--keep-camera leaves them locked).

Tiles: each is the average of --frames 4 uncompressed frames (usb_cam.py /frame), blurred
ones left out, saved as JPEG 95: less noise than one stream JPEG. After a move the frames are
taken as soon as the picture holds still (--settle is the most it waits), and the head goes on
to the next tile while the last one is averaged and saved. Each tile is appended to tiles.log;
tiles.json is rewritten every 25 tiles and at the end (--resume reads both).
"""

import argparse
import http.client
import json
import math
import os
import socket
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:
    sys.exit("OpenCV missing: pip install opencv-python")

SCANS = Path(__file__).resolve().parent.parent / "scans"  # the repo root, from anywhere
CALIB = SCANS / "calibration.json"  # mm_per_px, rot, flip_x, flip_y, image_size
LENS = SCANS / "lens.json"  # camera matrix + distortion from --lens-calibrate
SETTINGS = SCANS / "scan_settings.json"  # head_area, grid: defaults for every scan (the page)


JPEG_MAX = 65500  # JPEG can't be wider or taller: a bigger collage is saved as PNG
CLIP = 250  # a channel at or above this is blown out (no detail left)
SAVE_EVERY = 25  # tiles.json rewritten every this many tiles (each one goes to tiles.log first)


def pmap(fn, items):
    """fn over items in threads, results in order. OpenCV and numpy release the GIL, so the
    per-tile and per-pair work runs on all cores."""
    items = list(items)
    if len(items) < 2:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(min(len(items), os.cpu_count() or 4)) as ex:
        return list(ex.map(fn, items))


# ---------------------------------------------------------------- HTTP
class Http:
    """One kept-alive connection to a server. urllib opened a new TCP connection for every
    request: a handshake per state poll (10 a second while moving) and per frame, and one
    more socket for the bridge's small web server each time. A server that closes after
    every answer (HTTP/1.0) still works: http.client then reconnects by itself."""

    def __init__(self, base, timeout=10):
        u = urllib.parse.urlsplit(base)
        self.cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        self.host, self.port, self.prefix = u.hostname, u.port, u.path.rstrip("/")
        self.timeout = timeout
        self.conn = None
        self.lock = threading.Lock()

    def request(self, path, data=None, headers=None):
        """(status, headers, body). data: bytes -> POST. A connection the server closed while
        it sat unused is retried once on a new one. That can repeat a POST the server did get,
        which is harmless here: the bridge only gets absolute moves (G90 G0 + M400 M114), the
        camera only absolute settings."""
        with self.lock:
            for retry in (False, True):
                reused = self.conn is not None
                if not reused:
                    self.conn = self.cls(self.host, self.port, timeout=self.timeout)
                try:
                    self.conn.request("GET" if data is None else "POST", self.prefix + path,
                                      body=data, headers=headers or {})
                    r = self.conn.getresponse()
                    body = r.read()
                    if r.will_close:
                        self.conn.close()
                        self.conn = None
                    return r.status, r.headers, body
                except (ConnectionError, http.client.HTTPException, OSError) as e:
                    self.conn.close()
                    self.conn = None
                    stale = isinstance(e, (ConnectionError, http.client.BadStatusLine))
                    if retry or not reused or not stale:
                        if isinstance(e, http.client.HTTPException):
                            raise ConnectionError(f"{type(e).__name__}: {e}") from e
                        raise


def resolve(base):
    """The URL with its host looked up once, IPv4: on Windows "localhost" tries IPv6 first
    (2 s lost per request to an IPv4-only server), and m200.local is an mDNS lookup."""
    u = urllib.parse.urlsplit(base.rstrip("/"))
    try:
        ip = socket.getaddrinfo(u.hostname, None, socket.AF_INET)[0][4][0]
    except (OSError, TypeError):
        return base.rstrip("/")
    return u._replace(netloc=ip + (f":{u.port}" if u.port else "")).geturl()


class Bridge:
    """The bridge's REST API: state, settings, G-code batches."""

    def __init__(self, base, token=None):
        self.base = resolve(base)
        self.http = Http(self.base)
        self.headers = {"X-Token": token} if token else {}

    def state(self):
        return json.loads(self.http.request("/api/state")[2])

    def settings(self):
        return json.loads(self.http.request("/api/settings")[2])["v"]

    def gcode(self, lines):
        """Send one batch (whole or nothing); retried while the queue is full."""
        body = "\n".join(lines).encode()
        for _ in range(50):
            code, _, out = self.http.request("/api/gcode", body,
                                             {**self.headers, "Content-Type": "text/plain"})
            if code == 503:  # queue full: nothing was sent
                time.sleep(0.2)
                continue
            if code in (401, 403):
                sys.exit(f"bridge refused the G-code ({code}): --token or M200_TOKEN needed")
            n = json.loads(out).get("queued", 0)
            if n != len(lines):
                raise RuntimeError(f"the bridge took {n} of {len(lines)} lines "
                                   "(guard refusal: see the page's console)")
            return
        raise RuntimeError("bridge queue stays full")

    def move_to(self, x, y, feed, st, idle=None):
        """G0 to machine mm x, y (from the switches) and wait until done: M400 holds the M114
        until the move has ended, so its answer (state "pos") equals the target only then."""
        tx = st["zero"][0] + st["dir"][0] * x
        ty = st["zero"][1] + st["dir"][1] * y
        self.gcode(["G90", f"G0 X{tx:.2f} Y{ty:.2f} F{feed:.0f}", "M400", "M114"])
        cur = st.get("mpos") or [x, y]
        dist = math.hypot(x - (cur[0] or 0), y - (cur[1] or 0))
        deadline = time.monotonic() + dist / feed * 60 * 2 + 15
        while time.monotonic() < deadline:
            s = self.state()
            if s["stopped"] or s["halted"] or s["link"] != "up":
                raise RuntimeError("printer stopped, halted or not answering")
            p = s.get("pos")
            if s["queue"] == 0 and p and abs(p[0] - tx) < 0.05 and abs(p[1] - ty) < 0.05:
                return s
            if idle:
                idle()  # keeps the --show window alive
            time.sleep(0.1)
        raise RuntimeError(f"move to X{x:.2f} Y{y:.2f} did not finish in time")


class Cam:
    """tools/usb_cam.py: /frame, /snapshot and /controls."""

    STILL_PX = 0.5  # frame-to-frame shift (full-size px) below which the picture holds still

    def __init__(self, base):
        self.base = resolve(base)
        self.http = Http(self.base)
        self.has_frame = None  # /frame there? (None: not asked yet)

    def snapshot(self):
        code, _, jpg = self.http.request("/snapshot")
        if code != 200:
            raise RuntimeError(f"camera snapshot failed ({code})")
        return jpg

    def fresh(self, settle=0.0):
        """A JPEG taken after this call: one is thrown away (it can be from before the move),
        then the next different one is kept. Only for a camera without /frame."""
        time.sleep(settle)
        old = self.snapshot()
        for _ in range(100):
            time.sleep(0.05)
            jpg = self.snapshot()
            if jpg != old:
                return jpg
        raise RuntimeError("camera gives no new frame")

    def frame_after(self, seq):
        """(seq, image) of the first frame newer than seq (-1: the newest), or None if the
        camera server has no /frame (older usb_cam.py, ESP32-CAM). fmt=raw: the pixels as they
        are, no PNG to encode and decode; an older usb_cam.py ignores it and sends PNG."""
        if self.has_frame is False:
            return None
        code, h, body = self.http.request(f"/frame?after={seq}&fmt=raw")
        if code == 404:
            self.has_frame = False
            return None
        if code != 200:
            raise RuntimeError(f"camera frame failed ({code})")
        self.has_frame = True
        seq = int(h.get("X-Frame-Seq", seq + 1))
        if h.get("X-Width"):
            shape = int(h["X-Height"]), int(h["X-Width"]), int(h.get("X-Channels", 3))
            img = np.frombuffer(body, np.uint8).reshape(shape) if len(body) == math.prod(shape) else None
        else:
            img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("camera sent a frame that can't be read")
        return seq, img

    def frames(self, n, settle=0.0, still=False):
        """n uncompressed frames, or None without /frame. The newest frame is thrown away
        first (it can be from before the move).
        still=False: all taken after waiting `settle` s (for exposure / focus to change).
        still=True: as soon as the picture holds still (moved < STILL_PX between frames, n in
        a row, at least 2), at most `settle` s: after that the next n are taken as they come,
        as the fixed wait did. A short move stops shaking well before the long row change."""
        if not still:
            time.sleep(settle)
        got = self.frame_after(-1)
        if got is None:
            return None
        seq = got[0]
        deadline = time.monotonic() + settle if still else 0.0
        need = max(n, 2) if still else n
        run, prev = [], None
        while len(run) < need:
            seq, img = self.frame_after(seq)
            if time.monotonic() < deadline:
                small = still_gray(img)
                if prev is None or frame_shift(prev, small) > self.STILL_PX / 2:  # half size
                    run = []
                prev = small
            run.append(img)
        return run[-n:]

    def image(self, settle=0.0, still=False):
        frames = self.frames(1, settle, still)
        if frames:
            return frames[0]
        return cv2.imdecode(np.frombuffer(self.fresh(settle), np.uint8), cv2.IMREAD_COLOR)

    def grab_tile(self, n, settle):
        """What a tile is made of, taken now (the head can move on once this returns): n frames,
        or JPEG bytes from a camera without /frame. make_tile() turns it into the tile."""
        frames = self.frames(n, settle, still=True)
        return frames if frames is not None else self.fresh(settle)

    def controls(self):
        code, _, out = self.http.request("/controls")
        return {c["key"]: c for c in json.loads(out)["controls"]} if code == 200 else {}

    def set(self, changes):
        self.http.request("/controls", json.dumps(changes).encode(),
                          {"Content-Type": "application/json"})


def make_tile(grabbed):
    """One tile, JPEG bytes (quality 95): the average of the frames with the blurred ones left
    out (the head still shaking). Averaging n still frames cuts the sensor noise by sqrt(n)
    and has no JPEG blocks. Runs in a worker thread while the head goes to the next tile."""
    if isinstance(grabbed, bytes):  # a camera without /frame: its JPEG as it is
        return grabbed
    sharp = [sharpness(f) for f in grabbed]
    keep = [f for f, s in zip(grabbed, sharp) if s >= 0.8 * max(sharp)]
    img = keep[0] if len(keep) == 1 else \
        np.mean(np.stack(keep).astype(np.float32), 0).round().astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError("could not encode the tile")
    return buf.tobytes()


def still_gray(img):
    """Half-size grey, float32: enough to see whether the picture still moves."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (g.shape[1] // 2, g.shape[0] // 2), interpolation=cv2.INTER_AREA) \
        .astype(np.float32)


def frame_shift(a, b):
    """How far (px) the picture moved from a to b."""
    (dx, dy), _ = cv2.phaseCorrelate(a, b, hanning(a.shape[1], a.shape[0]))
    return math.hypot(dx, dy)


@lru_cache(maxsize=32)
def hanning(w, h):
    """The window for phase correlation, made once per size (read only)."""
    win = cv2.createHanningWindow((w, h), cv2.CV_32F)
    win.flags.writeable = False
    return win


# ---------------------------------------------------------------- camera settings
def brightness(img):
    return float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean())


def sharpness(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    return float(cv2.Laplacian(g[h // 4:3 * h // 4, w // 4:3 * w // 4], cv2.CV_64F).var())


def focus_search(cam, focus):
    """Manual focus at the sharpest value: coarse, then fine around the best."""
    lo, hi = focus["min"], focus["max"]
    for n in (10, 8):
        vals = sorted({int(round(v)) for v in np.linspace(lo, hi, n)})
        scores = []
        for v in vals:
            cam.set({"focus": {"auto": False, "value": v}})
            scores.append(sharpness(cam.image(0.4)))
        i = int(np.argmax(scores))
        lo, hi = vals[max(0, i - 1)], vals[min(len(vals) - 1, i + 1)]
    cam.set({"focus": {"auto": False, "value": vals[i]}})
    print(f"focus set to {vals[i]} (sharpest)")


def clipped(img):
    """Share of the picture blown out (some channel at CLIP or above)."""
    return float((img.max(2) >= CLIP).mean())


def highlight_exposure(cam, ctl, value):
    """Auto exposure judges the average, and the dark hole plate pulls it up: white paper and
    the ruler then burn out (no detail left to recover). Step darker while more than 0.5 %
    is blown out, at most two steps (DirectShow's exposure is log2 seconds: one step = half
    the light; other cameras: 0.7x, 0.5x) and never below 40 % of the auto brightness; the
    collage brightens the result again (colour_gains). Leaves the exposure set, returns it."""
    img = cam.image(0.6)
    clip0, b0 = clipped(img), brightness(img)
    if clip0 <= 0.005:
        print(f"  highlights fine ({100 * clip0:.1f} % blown out): exposure stays")
        return value
    log_steps = ctl["max"] - ctl["min"] <= 30
    steps = [value - 1, value - 2] if log_steps else [int(value * 0.7), int(value * 0.5)]
    chosen, clip_at = value, clip0
    for v in (s for s in steps if s >= ctl["min"]):
        cam.set({"exposure": {"auto": False, "value": v}})
        img = cam.image(0.6)
        cl, b = clipped(img), brightness(img)
        if b < 0.4 * b0:
            break  # too dark: the last one stays
        chosen, clip_at = v, cl
        if cl <= 0.005:
            break
    cam.set({"exposure": {"auto": False, "value": chosen}})
    print(f"  highlights: {100 * clip0:.1f} % blown out at {value}, {100 * clip_at:.1f} % at "
          f"{chosen}: exposure {chosen} (--meter mean: as bright as auto)")
    return chosen


def lock_camera(cam, a, before):
    """Auto exposure / white balance / focus off. Fills `before` with what to put back first,
    so a Ctrl+C in the middle still restores them."""
    ctl = cam.controls()
    if not ctl:
        print("camera has no /controls: its settings stay as they are")
        return
    keys = [k for k in ("exposure", "wb", "focus") if k in ctl]
    before.update({k: {"value": ctl[k]["value"], "auto": ctl[k].get("auto", False)} for k in keys})
    if "focus" in ctl:  # first, before exposure changes give autofocus a reason to hunt
        if a.focus is not None:
            cam.set({"focus": {"auto": False, "value": a.focus}})
        elif ctl["focus"].get("auto"):
            # hold the focus autofocus has now: usb_cam.py switches auto off and writes no value,
            # so the lens stays; the sharpness check below catches a driver that moves it anyway
            s0 = sharpness(cam.image(0.3))
            cam.set({"focus": {"auto": False}})
            s1 = sharpness(cam.image(0.5))
            held = cam.controls().get("focus", {}).get("value")
            if s1 >= 0.7 * s0:
                print(f"focus held where autofocus had it: {held}")
            else:  # the driver moved the lens anyway: find the sharpest by hand
                print(f"focus moved when locked ({s1:.0f} vs {s0:.0f} sharpness): searching")
                focus_search(cam, ctl["focus"])
    if "exposure" in ctl:
        if a.exposure is not None:
            cam.set({"exposure": {"auto": False, "value": a.exposure}})
        elif ctl["exposure"].get("auto"):
            # the slider value isn't what auto picked: find the manual value as bright as auto
            goal = brightness(cam.image(0.3))
            lo, hi = ctl["exposure"]["min"], ctl["exposure"]["max"]
            best = None
            while lo <= hi:
                mid = (lo + hi) // 2
                cam.set({"exposure": {"auto": False, "value": mid}})
                b = brightness(cam.image(0.6))
                if best is None or abs(b - goal) < best[1]:
                    best = (mid, abs(b - goal))
                lo, hi = (mid + 1, hi) if b < goal else (lo, mid - 1)
            cam.set({"exposure": {"auto": False, "value": best[0]}})
            print(f"exposure locked at {best[0]} (as bright as auto: {goal:.0f}/255)")
            if getattr(a, "meter", "highlights") == "highlights":
                highlight_exposure(cam, ctl["exposure"], best[0])
        else:
            print(f"exposure stays at {ctl['exposure']['value']}")
    if "wb" in ctl:
        cam.set({"wb": {"auto": False, **({"value": a.wb} if a.wb is not None else {})}})
    final = cam.controls()
    stuck = [k for k in keys if final[k].get("auto")]
    if stuck:  # DirectShow refused the change: the camera is in a bad state or busy
        print(f"WARNING: the camera did not take manual {', '.join(stuck)} (still auto): tiles "
              "may differ. Restart usb_cam.py (close the Angetube app) if this stays.")
    print("camera: " + ", ".join(f"{k} {final[k]['value']}"
                                 f"{' auto' if final[k].get('auto') else ''}" for k in keys))


# ---------------------------------------------------------------- geometry
def orient(img, flip_x=False, flip_y=False, rot=0):
    """Like the page: mirror, then rotate clockwise; then the extra vertical flip."""
    if flip_x:
        img = cv2.flip(img, 1)
    if rot:
        img = cv2.rotate(img, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                               270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rot])
    if flip_y:
        img = cv2.flip(img, 0)
    return img


def orient_vec(v, flip_x, rot):
    """The same transform for a shift (x right, y down)."""
    x, y = v
    if flip_x:
        x = -x
    for _ in range(rot // 90):
        x, y = -y, x  # 90 clockwise on screen
    return x, y


def plan_grid(area, fov, overlap, counts=None):
    """Tile centres over area (x0, y0, x1, y1), snake order: [(row, col, cx, cy)]. counts
    (cols, rows) fixes the number of tiles; else as few as give at least `overlap`."""
    def axis(a0, a1, f, n=None):
        span = a1 - a0
        if span <= f and not n:
            return [(a0 + a1) / 2]
        n = n or math.ceil((span - f) / (f * (1 - overlap))) + 1
        if n == 1:
            return [(a0 + a1) / 2]
        step = (span - f) / (n - 1)
        return [a0 + f / 2 + i * step for i in range(n)]
    xs = axis(area[0], area[2], fov[0], counts and counts[0])
    ys = axis(area[1], area[3], fov[1], counts and counts[1])
    tiles = []
    for r, y in enumerate(ys):
        cols = list(enumerate(xs))
        for c, x in (cols if r % 2 == 0 else cols[::-1]):
            tiles.append((r, c, x, y))
    return tiles


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_json(path, obj):
    """Written to a temporary file, then put in place: a crash mid-write can't leave half a
    tiles.json. (Windows refuses the swap while another program has the file open, e.g.
    usb_cam.py's page reading it: then it is written in place as before.)"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=1)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    path.write_text(text, encoding="utf-8")
    tmp.unlink(missing_ok=True)


def load_meta(folder):
    """A scan's tiles.json with the tiles in tiles.log added (taken since it was last
    written: see TileLog)."""
    meta = load_json(Path(folder) / "tiles.json")
    try:
        lines = (Path(folder) / "tiles.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return meta
    by_file = {t["file"]: t for t in meta.get("tiles", [])}
    for line in lines:
        try:
            t = json.loads(line)
        except ValueError:
            continue  # the last line cut off by a crash
        by_file[t["file"]] = t
    if meta:
        meta["tiles"] = list(by_file.values())
    return meta


class TileLog:
    """The tiles of a running scan. Rewriting the whole tiles.json after every tile costs more
    with every tile (a long scan writes the list N times over); instead each tile is one line
    appended to tiles.log, and tiles.json is rewritten every SAVE_EVERY tiles and at the end.
    load_meta() puts the two together, so --resume after a crash still skips every tile."""

    def __init__(self, folder, meta):
        self.folder, self.meta = folder, meta
        self.by_file = {t["file"]: t for t in meta["tiles"]}
        self.unsaved = 0

    def add(self, tile):
        self.by_file[tile["file"]] = tile
        with open(self.folder / "tiles.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(tile) + "\n")
        self.unsaved += 1
        if self.unsaved >= SAVE_EVERY:
            self.save()

    def save(self):
        self.meta["tiles"] = list(self.by_file.values())
        save_json(self.folder / "tiles.json", self.meta)
        (self.folder / "tiles.log").unlink(missing_ok=True)
        self.unsaved = 0


# ---------------------------------------------------------------- collage
# A tile is placed with B: a 2x2 matrix, mm on the bed per raw picture pixel (x right, y down,
# from the picture centre). It holds the scale, the rotation (any angle) and the mirror.
# bed point = camera centre (cx, cy) + B @ (pixel - centre).
def undistorter(meta, size):
    """The lens correction as a function, or None. The pixel map is computed once here
    (cv2.undistort computes it again for every picture)."""
    lens = meta.get("lens")
    if not lens:
        return None
    K, dist = np.array(lens["K"], float), np.array(lens["dist"], float)
    m1, m2 = cv2.initUndistortRectifyMap(K, dist, None, K, size, cv2.CV_16SC2)
    return lambda img: cv2.remap(img, m1, m2, cv2.INTER_LINEAR)


class TileSet:
    """A scan's saved tiles, read (and undistorted) once for every collage stage: before, each
    stage read every tile again, up to ~13 times when the mapping needed its fallback starts.
    Kept: a grey copy of each (uint8, 1 byte/px) and, while they fit in COLOUR_BUDGET bytes,
    the colour pictures (else build_collage reads them once more). A tile that can't be read
    or has another size is left out here, for every stage."""

    COLOUR_BUDGET = 1.5e9

    def __init__(self, folder, meta):
        self.folder = folder
        listed = [t for t in meta["tiles"] if (folder / t["file"]).exists()]
        first = next((im for im in (cv2.imread(str(folder / t["file"])) for t in listed)
                      if im is not None), None)
        if first is None:
            raise RuntimeError("no tiles saved")
        self.h, self.w = first.shape[:2]
        self.undistort = undistorter(meta, (self.w, self.h))
        keep = len(listed) * self.w * self.h * 3 <= self.COLOUR_BUDGET

        def load(t):
            img = cv2.imread(str(folder / t["file"]))
            if img is None or img.shape[:2] != (self.h, self.w):
                return None
            if self.undistort:
                img = self.undistort(img)
            clipped = (img.max(2) >= CLIP).astype(np.float32)  # blown out in any channel
            clip = cv2.resize(clipped, self.small_size, interpolation=cv2.INTER_AREA)
            return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (img if keep else None), clip
        self.small_size = (max(8, self.w // 4), max(8, self.h // 4))
        self.tiles, self.gray, self._colour, self._small = [], {}, {}, {}
        self.clip = {}  # {file: share of blown-out pixels, quarter size}
        for t, got in zip(listed, pmap(load, listed)):
            if got is None:
                print(f"  {t['file']}: unreadable or another size, left out")
                continue
            self.tiles.append(t)
            self.gray[t["file"]], self._colour[t["file"]], self.clip[t["file"]] = got
        if not self.tiles:
            raise RuntimeError("no tiles saved")

    def colour(self, file):
        img = self._colour.get(file)
        if img is None:
            img = cv2.imread(str(self.folder / file))
            if self.undistort:
                img = self.undistort(img)
        return img

    def grays(self, scale=1.0):
        """{file: grey uint8}, shrunk by `scale` (made once per scale)."""
        if scale == 1.0:
            return self.gray
        if scale not in self._small:
            size = (round(self.w * scale), round(self.h * scale))
            shrink = lambda g: cv2.resize(g, size, interpolation=cv2.INTER_AREA)
            self._small[scale] = dict(zip(self.gray, pmap(shrink, self.gray.values())))
        return self._small[scale]


def b_from_params(meta):
    """B from mm_per_px + rot/flip: the oriented picture's right is +X, its top +Y."""
    ex = orient_vec((1, 0), meta["flip_x"], meta["rot"])
    ey = orient_vec((0, 1), meta["flip_x"], meta["rot"])
    fy = -1 if meta["flip_y"] else 1
    return meta["mm_per_px"] * np.array([[ex[0], ey[0]], [-fy * ex[1], -fy * ey[1]]], float)


def b_describe(B):
    """(mm/px, angle of the picture's x axis on the bed in degrees, mirrored)."""
    return (math.sqrt(abs(np.linalg.det(B))), math.degrees(math.atan2(B[1, 0], B[0, 0])),
            np.linalg.det(B) > 0)  # det < 0 is the normal case (picture y down, bed Y up)


@lru_cache(maxsize=4)
def spectrum_masks(n):
    """lattice()'s fixed arrays for an n x n picture, made once (read only): the spectrum
    bins to ignore, each bin's angle, the window."""
    c = n // 2
    yy, xx = np.mgrid[-c:n - c, -c:n - c]
    r = np.hypot(xx, yy)
    drop = (r < 8) | (r > n / 4) | (yy < 0) | ((yy == 0) & (xx < 0))  # one half: no twin peaks
    ang = np.degrees(np.arctan2(yy, xx)) % 180
    win = cv2.createHanningWindow((n, n), cv2.CV_32F)
    for arr in (drop, ang, win):
        arr.flags.writeable = False
    return drop, ang, win


def lattice(img):
    """The plate's hole grid in one picture (colour or grey): [(period px, angle deg mod 180)]
    for its two strongest directions (2D spectrum peaks, sub-pixel). The hole spacing is a
    fixed size on the plate, so it gives the scale of any picture; the angle gives the
    camera's turn up to the grid's own 90 deg symmetry. Light and pixel noise are filtered
    out first."""
    g = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img).astype(np.float32)
    g = g - cv2.GaussianBlur(g, (0, 0), 6)
    h, w = g.shape
    n = min(h, w)
    sq = g[(h - n) // 2:(h - n) // 2 + n, (w - n) // 2:(w - n) // 2 + n]
    drop, ang_map, win = spectrum_masks(n)
    d = cv2.dft(sq * win, flags=cv2.DFT_COMPLEX_OUTPUT)  # ~15x numpy's fft2, frees the GIL
    F = np.fft.fftshift(cv2.magnitude(d[..., 0], d[..., 1]))
    c = n // 2
    F[drop] = 0
    out = []
    for _ in range(2):
        i = np.unravel_index(np.argmax(F), F.shape)
        if F[i] <= 0 or not 1 <= i[0] < n - 1 or not 1 <= i[1] < n - 1:
            break
        win3 = F[i[0] - 1:i[0] + 2, i[1] - 1:i[1] + 2]
        wy, wx = np.mgrid[-1:2, -1:2]
        ky = i[0] - c + (win3 * wy).sum() / win3.sum()
        kx = i[1] - c + (win3 * wx).sum() / win3.sum()
        out.append((n / np.hypot(kx, ky), math.degrees(math.atan2(ky, kx)) % 180))
        F[np.abs((ang_map - out[-1][1] + 90) % 180 - 90) < 20] = 0  # next: another direction
    return out


def scan_lattice(ts):
    """(period px, angle deg mod 90) of the hole grid over a scan's tiles (medians), or None."""
    per, vec = [], []
    for lat in pmap(lattice, ts.gray.values()):
        if len(lat) == 2 and abs(lat[0][0] / lat[1][0] - 1) < 0.05:  # a square grid seen
            per.append((lat[0][0] + lat[1][0]) / 2)
            a = math.radians(lat[0][1] * 4)  # mod 90: average on the 4x circle
            vec.append((math.cos(a), math.sin(a)))
    if len(per) < max(2, len(ts.tiles) // 3):
        return None
    m = np.median(np.array(vec), 0)
    return float(np.median(per)), math.degrees(math.atan2(m[1], m[0])) / 4 % 90


def picture_lattice(img):
    """scan_lattice for one picture: (period px, angle mod 90) or None."""
    lat = lattice(img)
    if len(lat) == 2 and abs(lat[0][0] / lat[1][0] - 1) < 0.05:
        return (lat[0][0] + lat[1][0]) / 2, lat[0][1] % 90
    return None


def rot2(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


def nearest_params(B):
    """The rot / flip_x (90 deg steps) closest to B: how to show its tiles upright."""
    s = b_describe(B)[0]
    best = min(((r, fx) for r in (0, 90, 180, 270) for fx in (False, True)),
               key=lambda o: np.linalg.norm(B / s - b_from_params(
                   {"mm_per_px": 1, "rot": o[0], "flip_x": o[1], "flip_y": False})))
    return best


def overlap_shift(g1, g2, pred):
    """Content shift (px) from tile 1 to tile 2: the strip both should show (from `pred`) is cut
    from each and phase-correlated; returns (shift, strength) or None if they barely overlap.
    Dirt and edges give the peak; the plate's holes alone repeat, hence the strength check."""
    h, w = g1.shape
    px, py = int(round(pred[0])), int(round(pred[1]))
    x0, x1 = max(0, -px), min(w, w - px)
    y0, y1 = max(0, -py), min(h, h - py)
    if x1 - x0 < 40 or y1 - y0 < 40:
        return None
    a = g1[y0:y1, x0:x1].astype(np.float32)
    b = g2[y0 + py:y1 + py, x0 + px:x1 + px].astype(np.float32)
    (rx, ry), strength = cv2.phaseCorrelate(a, b, hanning(a.shape[1], a.shape[0]))
    return np.array([px + rx, py + ry]), strength


def neighbour_pairs(ts):
    """[(file, its right or upper neighbour's file, head move dh mm)]."""
    tiles = {(t["row"], t["col"]): t for t in ts.tiles}
    out = []
    for (r, c), t in tiles.items():
        for nb in ((r, c + 1), (r + 1, c)):
            if nb in tiles:
                u = tiles[nb]
                out.append((t["file"], u["file"], np.array([u["cx"] - t["cx"], u["cy"] - t["cy"]])))
    return out


def pair_shifts(ts, B, scale=1.0, pairs=None):
    """[(file1, file2, dh, content shift, strength)] for the neighbour pairs, predicted by B,
    measured on the pictures shrunk by `scale`, the shift in full-size px. In parallel."""
    gray = ts.grays(scale)
    Binv = np.linalg.inv(B)

    def one(p):
        f1, f2, dh = p
        got = overlap_shift(gray[f1], gray[f2], -(Binv @ dh) * scale)
        return None if got is None else (f1, f2, dh, got[0] / scale, got[1])
    return [g for g in pmap(one, neighbour_pairs(ts) if pairs is None else pairs) if g]


def coarse_scale(ts, B):
    """0.5 when every neighbour overlap is at least 160 px wide (80 at half size: plenty for
    phase correlation), else 1: the mapping's starting points are then tried on half-size
    pictures (a quarter of the pixels), and only the winner is measured on the full ones."""
    Binv = np.linalg.inv(B)
    widths = [min(ts.w - abs(s[0]), ts.h - abs(s[1]))
              for s in (Binv @ dh for _, _, dh in neighbour_pairs(ts))]
    return 0.5 if widths and min(widths) >= 160 else 1.0


def fit_b(ts, B0, scale=1.0, min_strength=0.06, passes=3):
    """Measure B from the overlaps of neighbouring tiles: a head move dh shows as a content
    shift -inv(B) @ dh. `passes` passes, each predicting from the last fit (one is enough from
    a B already measured at half size); pairs far off the fit are dropped. Returns (B, pairs
    used, rms px, the last pass's pair_shifts) or None."""
    pairs = neighbour_pairs(ts)
    B, used, raw = B0, [], []
    for _ in range(passes):
        raw = pair_shifts(ts, B, scale, pairs)
        used = [(dh, sh) for _, _, dh, sh, s in raw if s >= min_strength]
        for _ in range(2):  # least squares, then again without the pairs it can't explain
            H = np.array([u[0] for u in used]) if used else np.zeros((0, 2))
            if len(used) < 3 or np.linalg.matrix_rank(H) < 2:
                return None
            K = np.linalg.lstsq(H, np.array([u[1] for u in used]), rcond=None)[0].T  # shift = K dh
            err = np.array([np.hypot(*(K @ dh - sh)) for dh, sh in used])
            used = [u for u, e in zip(used, err) if e <= max(4.0, 3 * np.median(err))]
        B = -np.linalg.inv(K)
    rms = float(np.sqrt(np.mean([np.sum((K @ dh - sh) ** 2) for dh, sh in used])))
    return B, len(used), rms, raw


class CameraModel:
    """Picture pixel -> bed mm, measured from the picture centre's bed point. First the lens
    (radial distortion k1: barrel < 0, pincushion > 0), then a homography that keeps the
    centre in place: a camera not looking straight down sees the far side smaller (keystone),
    which a scale + rotation (B) can't follow: the tiles then only match in the middle of each
    overlap. The camera is fixed on the head, so one model holds for every tile.
    x = (pixel - centre) / F, x_u = x (1 + k1 |x|^2), bed = M2 x_u / (1 + g . x_u)."""

    def __init__(self, size, M2, g=(0.0, 0.0), k1=0.0):
        self.size = tuple(int(v) for v in size)
        w, h = self.size
        self.c = np.array([(w - 1) / 2, (h - 1) / 2])
        self.F = max(w, h) / 2
        self.M2 = np.array(M2, float).reshape(2, 2)
        self.g = np.array(g, float)
        self.k1 = float(k1)

    @classmethod
    def from_b(cls, B, size):
        return cls(size, np.asarray(B, float) * (max(size) / 2))

    @classmethod
    def from_params(cls, size, p):
        return cls(size, p[:4], p[4:6], p[6])

    @classmethod
    def from_json(cls, d):
        return cls(d["size"], d["M2"], d["g"], d["k1"])

    def params(self):
        return np.concatenate([self.M2.ravel(), self.g, [self.k1]])

    def to_json(self):
        return {"size": list(self.size), "M2": self.M2.tolist(), "g": self.g.tolist(),
                "k1": self.k1}

    def b(self):
        """The model near the picture centre as B (mm per px, 2x2)."""
        return self.M2 / self.F

    def to_bed(self, p):
        """(..., 2) pixels -> (..., 2) mm from the centre's bed point."""
        x = (np.asarray(p, float) - self.c) / self.F
        x = x * (1 + self.k1 * (x * x).sum(-1, keepdims=True))
        return (x @ self.M2.T) / (1 + x @ self.g)[..., None]

    def to_pixel(self, f):
        """The inverse: (..., 2) mm from the centre's bed point -> pixels."""
        f = np.asarray(f, float)
        fx, fy = f[..., 0], f[..., 1]
        a, b = self.M2[0, 0] - fx * self.g[0], self.M2[0, 1] - fx * self.g[1]
        c, d = self.M2[1, 0] - fy * self.g[0], self.M2[1, 1] - fy * self.g[1]
        det = a * d - b * c
        xu = np.stack([(d * fx - b * fy) / det, (a * fy - c * fx) / det], -1)
        ru = np.sqrt((xu * xu).sum(-1))
        r = ru.copy()
        for _ in range(8):  # r (1 + k1 r^2) = ru, Newton
            r = r - (r * (1 + self.k1 * r * r) - ru) / (1 + 3 * self.k1 * r * r)
        k = np.where(ru > 1e-12, r / np.maximum(ru, 1e-12), 1.0)
        return self.c + xu * k[..., None] * self.F

    def border(self, n=24):
        """Points along the picture's edge (the corners alone miss a bowed edge)."""
        w, h = self.size
        t = np.linspace(0, 1, n)
        return np.concatenate([np.stack([t * (w - 1), 0 * t], 1), np.stack([t * (w - 1), 0 * t + h - 1], 1),
                               np.stack([0 * t, t * (h - 1)], 1), np.stack([0 * t + w - 1, t * (h - 1)], 1)])

    def describe(self):
        """(lens % at the corners: - barrel, + pincushion; keystone %: how much smaller the far
        edge is than the near one, side to side)."""
        w, h = self.size
        corner = ((w - 1) / 2) ** 2 + ((h - 1) / 2) ** 2
        lens = 100 * self.k1 * corner / self.F ** 2
        keystone = 100 * 2 * float(np.hypot(*self.g)) * max(w, h) / 2 / self.F
        return lens, keystone


def phase_peak(a, b, radius):
    """Shift d (px) with b(x) = a(x - d), and the peak's strength (5x5 sum, like
    cv2.phaseCorrelate: 1 for identical patches). Only shifts
    within `radius` of 0 count: the plate's holes repeat every ~11 px, so the match one hole
    off can be as strong; the model predicts well enough to stay within the right one."""
    win = hanning(a.shape[1], a.shape[0])
    A = cv2.dft(a * win, flags=cv2.DFT_COMPLEX_OUTPUT)
    Bf = cv2.dft(b * win, flags=cv2.DFT_COMPLEX_OUTPUT)
    R = cv2.mulSpectrums(Bf, A, 0, conjB=True)
    R /= (cv2.magnitude(R[..., 0], R[..., 1]) + 1e-9)[..., None]
    r = np.fft.fftshift(cv2.idft(R, flags=cv2.DFT_REAL_OUTPUT | cv2.DFT_SCALE))
    h, w = r.shape
    cy, cx, rad = h // 2, w // 2, int(min(radius, h // 2 - 2, w // 2 - 2))
    sub = r[cy - rad:cy + rad + 1, cx - rad:cx + rad + 1]
    iy, ix = np.unravel_index(int(np.argmax(sub)), sub.shape)
    y, x = cy - rad + iy, cx - rad + ix
    peak = float(np.maximum(r[max(0, y - 2):y + 3, max(0, x - 2):x + 3], 0).sum())  # as cv2 does
    win3 = np.maximum(r[y - 1:y + 2, x - 1:x + 2], 0)
    s = win3.sum()
    dy = (win3.sum(1) @ [-1, 0, 1]) / s if s > 0 else 0.0
    dx = (win3.sum(0) @ [-1, 0, 1]) / s if s > 0 else 0.0
    return np.array([x - cx + dx, y - cy + dy]), peak


def overlap_points(ts, model, centres, patch, radius, step, per_pair=24):
    """Matching points of neighbouring tiles: [(file1, file2, p1, p2, strength)], p1 a pixel of
    tile 1 and p2 where the same bed point is in tile 2. A grid of patches over each overlap
    (predicted by the model and positions), each matched within `radius`; at most per_pair
    patches a pair, spread over its overlap."""
    w, h = model.size
    m = patch // 2 + 1
    gx, gy = np.meshgrid(np.arange(m, w - m, step), np.arange(m, h - m, step))
    grid = np.stack([gx.ravel(), gy.ravel()], 1).astype(float)
    bed_grid = model.to_bed(grid)

    def one(pair):
        f1, f2, _ = pair
        p2 = model.to_pixel(bed_grid + np.subtract(centres[f1], centres[f2]))
        ok = np.flatnonzero((p2[:, 0] >= m) & (p2[:, 0] < w - m) & (p2[:, 1] >= m) & (p2[:, 1] < h - m))
        if len(ok) > per_pair:  # plenty for 7 numbers; a wide overlap would give hundreds
            ok = ok[np.linspace(0, len(ok) - 1, per_pair).round().astype(int)]
        g1, g2 = ts.gray[f1], ts.gray[f2]
        out = []
        for p1, q in zip(grid[ok].astype(int), np.round(p2[ok]).astype(int)):
            a = g1[p1[1] - patch // 2:p1[1] + patch // 2, p1[0] - patch // 2:p1[0] + patch // 2]
            b = g2[q[1] - patch // 2:q[1] + patch // 2, q[0] - patch // 2:q[0] + patch // 2]
            if a.std() < 3 or b.std() < 3:
                continue  # plain: nothing to match
            d, s = phase_peak(a.astype(np.float32), b.astype(np.float32), radius)
            out.append((f1, f2, p1.astype(float), q + d, s))
        return out
    return [p for got in pmap(one, neighbour_pairs(ts)) for p in got]


def spans_both_ways(pts, centres, least=20):
    """Enough matching points, from pairs in two directions (rows and columns): with only one,
    the model's scale across them is free and the fit can collapse."""
    if len(pts) < least:
        return False
    dirs = np.array([np.subtract(centres[p[1]], centres[p[0]]) for p in pts], float)
    dirs /= np.maximum(np.hypot(*dirs.T), 1e-9)[:, None]
    return np.linalg.svd(dirs, compute_uv=False)[1] ** 2 >= 0.1 * len(pts)


def fit_camera(ts, B, centres0):
    """The camera model and where each tile really was, from many matching points in every
    overlap (not one shift per overlap): least squares over the lens, the keystone and the
    tile positions together (positions held near where the head was sent). Three rounds,
    each matching again with the better model, closer (the hole grid repeats every ~11 px,
    so the first round only trusts strong, distinctive patches). Returns
    (model, {file: (cx, cy)}, [(file1, file2, mm left)], info) or None."""
    model = CameraModel.from_b(B, (ts.w, ts.h))
    centres = dict(centres0)
    s = b_describe(B)[0]
    files = [t["file"] for t in ts.tiles]
    idx = {f: k for k, f in enumerate(files)}
    cmd = np.array([centres0[f] for f in files], float)
    pts = []
    # patches small enough for the narrowest overlap (~35 % of 480 px is 170 px)
    for patch, radius, step, strong in ((128, 24, 48, 0.3), (96, 6, 32, 0.15), (64, 4, 24, 0.12)):
        for size in (patch, patch * 3 // 4, patch // 2):  # smaller when an overlap is narrow
            pts = [p for p in overlap_points(ts, model, centres, size, min(radius, size // 2 - 4),
                                             min(step, size // 2)) if p[4] >= strong]
            if spans_both_ways(pts, centres0):
                break
        else:
            return None  # e.g. no matches across the rows: Y would be unmeasured
        i1 = np.array([idx[p[0]] for p in pts])
        i2 = np.array([idx[p[1]] for p in pts])
        P1 = np.array([p[2] for p in pts])
        P2 = np.array([p[3] for p in pts])
        keep = np.ones(len(pts), bool)
        for _ in range(4):
            X = np.array([centres[f] for f in files])

            def resid(par, keep=keep, X=X):
                m = CameraModel.from_params(model.size, par)
                e = (X[i1] + m.to_bed(P1)) - (X[i2] + m.to_bed(P2))
                return e[keep].ravel()
            par = model.params()
            for _ in range(8):  # Gauss-Newton with damping, numeric Jacobian (7 parameters)
                e = resid(par)
                J = np.empty((len(e), len(par)))
                for k in range(len(par)):
                    dp = np.zeros_like(par)
                    dp[k] = 1e-6 * max(1.0, abs(par[k]))
                    J[:, k] = (resid(par + dp) - e) / dp[k]
                Hm = J.T @ J
                step_ = np.linalg.solve(Hm + 1e-3 * np.diag(np.diag(Hm) + 1e-12), -J.T @ e)
                if np.abs(resid(par + step_)).sum() < np.abs(e).sum():
                    par = par + step_
                else:
                    break
            model = CameraModel.from_params(model.size, par)
            # the positions, with the model held: each pair's mean says how far apart they are
            d = model.to_bed(P1) - model.to_bed(P2)  # x2 - x1 for each point
            edges, rhs = [], []
            for pair in set(zip(i1[keep], i2[keep])):
                sel = keep & (i1 == pair[0]) & (i2 == pair[1])
                if sel.sum() >= 3:
                    edges.append(pair)
                    rhs.append(np.median(d[sel], 0))
            if not edges:
                return None
            sol = solve_positions(len(files), edges, rhs, cmd, prior=0.3)
            centres = {f: (float(sol[k][0]), float(sol[k][1])) for k, f in enumerate(files)}
            X = sol
            e = np.hypot(*((X[i1] + model.to_bed(P1)) - (X[i2] + model.to_bed(P2))).T) / s  # px
            keep = e <= max(3.0, 3 * np.median(e[keep]))
        if not spans_both_ways([p for p, k in zip(pts, keep) if k], centres0):
            return None
    rms = float(np.sqrt(np.mean(e[keep] ** 2)))
    left = []
    for pair in sorted(set(zip(i1[keep], i2[keep]))):
        sel = keep & (i1 == pair[0]) & (i2 == pair[1])
        left.append((files[pair[0]], files[pair[1]], float(np.median(e[sel]) * s)))
    info = {"points": int(keep.sum()), "pairs": len(left), "rms_px": round(rms, 2)}
    return model, centres, left, info


def flat_field(ts):
    """The lighting every tile shares (a light on the head makes the same bright spot in each):
    the median of the blurred tiles, mean 1. Dividing by it evens the seams. None if < 4 tiles.
    The blur is wide (a 20th of the picture), so it is done at 1/8 size and scaled back up:
    64x less memory for the stack of all tiles, the same result."""
    if len(ts.tiles) < 4:
        return None
    size = (max(8, ts.w // 8), max(8, ts.h // 8))

    def one(g):
        s = cv2.resize(g, size, interpolation=cv2.INTER_AREA).astype(np.float32)
        return cv2.GaussianBlur(s, (0, 0), min(s.shape) / 20)
    flat = np.median(np.stack(pmap(one, ts.gray.values())), 0)
    flat = cv2.resize(flat, (ts.w, ts.h), interpolation=cv2.INTER_LINEAR)
    return flat / flat.mean()


def glare_map(ts):
    """Where the light's reflection sits in the picture (quarter size, 0..1), or None: the part
    blown out in many tiles at the same picture position. The bed moves through the picture
    from tile to tile, the light's reflection stays; white paper blown out in one tile is
    elsewhere in the next, so it doesn't count here (tile_quality handles that per tile)."""
    if len(ts.tiles) < 4:
        return None
    share = np.mean(np.stack([np.minimum(c * 3, 1) for c in ts.clip.values()]), 0)
    g = cv2.GaussianBlur(np.clip((share - 0.15) / 0.2, 0, 1), (0, 0), 2)
    return g if (g > 0.5).mean() >= 0.005 else None  # under 0.5 % of the picture: none


def tile_quality(ts, file, glare, base):
    """How well this tile shows each of its pixels: `base` (higher towards the centre, where
    the lens is sharpest and least distorted) cut down to 5 % in the light's reflection and
    by up to half where the tile is blown out. Another tile that saw the same spot cleanly
    then wins it. Blown-out areas count only as broad blurred patches: a few clipped pixels
    on white paper would otherwise make the choice jump between tiles and tear the picture
    into seams (each one visible wherever the tiles aren't perfectly aligned)."""
    clip = cv2.GaussianBlur(np.minimum(ts.clip[file] * 2, 1), (0, 0), 5)
    pen = 0.5 * np.clip(clip * 1.5, 0, 1)
    if glare is not None:
        pen = np.maximum(pen, 0.95 * glare)
    pen = cv2.resize(pen, (ts.w, ts.h), interpolation=cv2.INTER_LINEAR)
    return base * (1 - np.clip(pen, 0, 0.95))


def colour_gains(ts):
    """(b, g, r) gains that make the grey things grey (the aluminium plate, paper, the ruler):
    the median colour of the near-grey pixels, the "near-grey" test tightened three times
    after correcting by the last estimate, so a colour cast doesn't hide them and coloured
    things (blue handles, wood) don't count. Then a brightening (at most 2x) that puts the
    bright end just below white again, e.g. after --meter highlights made the tiles darker.
    None if there aren't enough grey pixels or nothing to change."""
    step = max(1, len(ts.tiles) // 12)
    px = []
    for t in ts.tiles[::step]:
        img = ts.colour(t["file"])[::4, ::4].reshape(-1, 3).astype(np.float32)
        px.append(img[(img.max(1) < CLIP - 5) & (img.min(1) > 25)])
    px = np.concatenate(px) if px else np.zeros((0, 3), np.float32)
    if len(px) < 1000:
        return None
    gains = np.ones(3)
    for limit in (0.3, 0.2, 0.12):
        q = px * gains
        m = q.mean(1, keepdims=True)
        grey = px[(np.abs(q - m).max(1) / m[:, 0]) < limit]
        if len(grey) < 0.02 * len(px):
            break
        med = np.median(grey, 0)
        gains = np.clip(med[1] / med, 0.7, 1.4)
    level = np.percentile((px * gains).max(1), 99.5)
    gains = gains * np.clip(240 / max(level, 1), 1.0, 2.0)
    return None if np.abs(gains - 1).max() < 0.02 else gains


class Canvas:
    """The collage's pixel grid on the bed: pixel (u, v)'s centre is at X = x0 + u*s,
    Y = y1 - v*s, in head mm (from the home switches, like the bridge's mpos)."""

    GRID = 8  # the tile maps are computed every GRID canvas px and interpolated (smooth)

    def __init__(self, x0, y1, s, W, H):
        self.x0, self.y1, self.s, self.W, self.H = x0, y1, s, W, H

    def tile_map(self, model, cx, cy, q=1, src_scale=1.0):
        """Where a tile whose centre sees bed (cx, cy) lands on the canvas (at 1/q size):
        ((u0, v0, u1, v1), map_x, map_y) for cv2.remap - canvas pixel -> tile pixel (of the
        tile shrunk by src_scale) - or None when it misses the canvas."""
        edge = model.to_bed(model.border()) + [cx, cy]
        W, H = math.ceil(self.W / q), math.ceil(self.H / q)
        off = (q - 1) / 2  # a small pixel's centre in full canvas pixels
        u = ((edge[:, 0] - self.x0) / self.s - off) / q
        v = ((self.y1 - edge[:, 1]) / self.s - off) / q
        u0, u1 = int(np.clip(np.floor(u.min()), 0, W)), int(np.clip(np.ceil(u.max()) + 1, 0, W))
        v0, v1 = int(np.clip(np.floor(v.min()), 0, H)), int(np.clip(np.ceil(v.max()) + 1, 0, H))
        if u1 <= u0 or v1 <= v0:
            return None
        return self.tile_map_box(model, cx, cy, (u0, v0, u1, v1), q, src_scale)

    def tile_map_box(self, model, cx, cy, box, q=1, src_scale=1.0):
        """tile_map for a given canvas box (u0, v0, u1, v1) at 1/q size."""
        u0, v0, u1, v1 = box
        off = (q - 1) / 2
        g = self.GRID
        nu, nv = math.ceil((u1 - u0 - 1) / g) + 2, math.ceil((v1 - v0 - 1) / g) + 2
        uu, vv = np.meshgrid(u0 + g * np.arange(nu), v0 + g * np.arange(nv))
        bed = np.stack([self.x0 + (uu * q + off) * self.s - cx,
                        self.y1 - (vv * q + off) * self.s - cy], -1)
        p = np.nan_to_num(model.to_pixel(bed), nan=-1e6, posinf=-1e6, neginf=-1e6)
        if src_scale != 1.0:
            p = (p + 0.5) * src_scale - 0.5
        size = (u1 - u0, v1 - v0)
        scale = np.float32([[1 / g, 0, 0], [0, 1 / g, 0]])  # node k sits at box pixel k*g
        maps = [cv2.warpAffine(p[..., k].astype(np.float32), scale, size,
                               flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_REPLICATE) for k in (0, 1)]
        return (u0, v0, u1, v1), maps[0], maps[1]

    def describe(self):
        return {"mm_per_px": self.s, "x0": round(self.x0, 4), "y1": round(self.y1, 4),
                "width": self.W, "height": self.H}


def plan_canvas(ts, model, centres, mmpp=None, bounds=None):
    """The canvas: at the camera's own mm/px (at the picture centre) or `mmpp`; over all
    tiles or only `bounds` (x0, y0, x1, y1 head mm, e.g. the travel). With a set mm/px, pixel
    centres sit on its multiples (0, 0.1, 0.2 ... mm), so collages of different scans line
    up pixel for pixel."""
    s = mmpp or math.sqrt(abs(np.linalg.det(model.b())))
    reach = model.to_bed(model.border())  # bed offsets of the picture's edge
    xs, ys = [p[0] for p in centres.values()], [p[1] for p in centres.values()]
    x0, x1 = min(xs) + reach[:, 0].min(), max(xs) + reach[:, 0].max()
    y0, y1 = min(ys) + reach[:, 1].min(), max(ys) + reach[:, 1].max()
    if bounds:
        x0, y0, x1, y1 = max(x0, bounds[0]), max(y0, bounds[1]), min(x1, bounds[2]), min(y1, bounds[3])
    if mmpp or bounds:
        x0, y1 = math.ceil(x0 / s - 1e-6) * s, math.floor(y1 / s + 1e-6) * s
        W, H = math.floor((x1 - x0) / s + 1e-6) + 1, math.floor((y1 - y0) / s + 1e-6) + 1
    else:
        W, H = math.ceil((x1 - x0) / s) + 1, math.ceil((y1 - y0) / s) + 1
    if W < 2 or H < 2:
        raise RuntimeError("the collage area is empty (crop outside the tiles?)")
    if W * H > 400e6:
        raise RuntimeError(f"canvas {W}x{H} too big: check --mm-per-px / --res")
    return Canvas(x0, y1, s, W, H)


def overlap_maps(ts, model, canvas, centres, q=4):
    """Each tile's canvas maps at 1/q size (for the steps below that look at overlaps):
    {file: ((u0, v0, u1, v1), map_x, map_y)}, maps into the tile shrunk 1/q."""
    files = [f for f in centres]
    got = pmap(lambda f: canvas.tile_map(model, *centres[f], q=q, src_scale=1 / q), files)
    return {f: g for f, g in zip(files, got) if g is not None}


def pair_overlap(maps, f1, f2):
    """The common box of two tiles at 1/q size and each tile's maps cut to it, or None."""
    (a0, b0, a1, b1), mx1, my1 = maps[f1]
    (c0, d0, c1, d1), mx2, my2 = maps[f2]
    u0, v0, u1, v1 = max(a0, c0), max(b0, d0), min(a1, c1), min(b1, d1)
    if u1 - u0 < 4 or v1 - v0 < 4:
        return None
    s1 = (slice(v0 - b0, v1 - b0), slice(u0 - a0, u1 - a0))
    s2 = (slice(v0 - d0, v1 - d0), slice(u0 - c0, u1 - c0))
    return (u0, v0, u1, v1), (mx1[s1], my1[s1]), (mx2[s2], my2[s2])


def disagreement(ts, maps, canvas, q=4):
    """How much neighbouring tiles disagree, per canvas pixel at 1/q size: the difference of
    their fine detail where both show it (brightness differences taken out). Small where the
    tiles line up; large on things standing up from the bed (each tile sees them from another
    angle) or where the alignment is off. Returns (err map, seen map, {(f1, f2): median})."""
    W, H = math.ceil(canvas.W / q), math.ceil(canvas.H / q)
    err = np.zeros((H, W), np.float32)
    seen = np.zeros((H, W), bool)
    small = ts.grays(1 / q)
    ones = np.ones(small[ts.tiles[0]["file"]].shape, np.float32)

    def detail(g, valid):
        """Fine detail: minus the local mean, taken only over the tile's own pixels (the black
        outside a tile's edge would otherwise show as a line of 'disagreement' along it)."""
        g = g.astype(np.float32) * valid
        mean = cv2.GaussianBlur(g, (0, 0), 2 * q) / np.maximum(cv2.GaussianBlur(valid, (0, 0), 2 * q), 1e-3)
        return g - mean

    out = {}
    for f1, f2, _ in neighbour_pairs(ts):
        if f1 not in maps or f2 not in maps:
            continue
        got = pair_overlap(maps, f1, f2)
        if got is None:
            continue
        (u0, v0, u1, v1), m1, m2 = got
        va = (cv2.remap(ones, *m1, cv2.INTER_LINEAR) > 0.999).astype(np.float32)
        vb = (cv2.remap(ones, *m2, cv2.INTER_LINEAR) > 0.999).astype(np.float32)
        both = cv2.erode((va * vb).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2).astype(bool)
        if both.sum() < 16:
            continue
        a = cv2.remap(small[f1], *m1, cv2.INTER_LINEAR)
        b = cv2.remap(small[f2], *m2, cv2.INTER_LINEAR)
        d = cv2.GaussianBlur(np.abs(detail(a, va) - detail(b, vb)), (0, 0), 1.5)
        e = err[v0:v1, u0:u1]
        np.maximum(e, np.where(both, d, 0), out=e)
        seen[v0:v1, u0:u1] |= both
        out[(f1, f2)] = float(np.median(d[both]))
    return err, seen, out


def seam_picture(err, seen, collage_img):
    """The disagreement coloured blue (tiles agree) .. red (off by a hole or more, or seen
    from another angle) over the collage in grey."""
    H, W = err.shape
    grey = cv2.cvtColor(cv2.resize(collage_img, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    pic = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
    heat = cv2.applyColorMap(np.clip(err * (255 / 40), 0, 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    pic[seen] = (0.35 * pic[seen] + 0.65 * heat[seen]).astype(np.uint8)
    return pic


def raised_things(err, seen):
    """Where the tiles disagree far more than this scan's plate does (3x its median, at least
    30): things standing up from the bed, seen from another angle in each tile. uint8 mask
    at the size of err, closed and with a small margin."""
    limit = max(30.0, 3 * float(np.median(err[seen]))) if seen.any() else 30.0
    blobs = (err > limit).astype(np.uint8)
    blobs = cv2.morphologyEx(blobs, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return cv2.dilate(blobs, np.ones((7, 7), np.uint8))  # a margin (~12 px): plain rims too


def keep_objects_whole(ts, maps, err, seen, glare=None, q=4):
    """Things standing up from the bed look different from each tile, so a seam through one
    shows a step. A region where the tiles disagree far more than this scan's plate does (3x
    its median, at least 30) is given to one tile: the one that sees most of it, nearest its
    centre and away from its glare. {file: [(y, x, bool mask)]} at 1/q size; build_collage
    takes the whole region from that tile. Regions bigger than half a tile are left alone:
    no single tile could supply them."""
    blobs = raised_things(err, seen)
    tile_area = ts.grays(1 / q)[ts.tiles[0]["file"]].size
    contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    small = [c for c in contours if cv2.contourArea(c) < 0.5 * tile_area]
    blobs[:] = 0
    cv2.drawContours(blobs, small, -1, 1, cv2.FILLED)  # plain insides (white tape) too
    n, lab, stats, _ = cv2.connectedComponentsWithStats(blobs, connectivity=8)
    ones = np.ones(ts.grays(1 / q)[ts.tiles[0]["file"]].shape, np.float32)
    h, w = ones.shape
    cover = {}
    for f, ((u0, v0, u1, v1), mx, my) in maps.items():  # where each tile sees, and how centrally
        inside = cv2.remap(ones, mx, my, cv2.INTER_NEAREST) > 0
        central = 1 - np.maximum(np.abs(mx / (w - 1) - 0.5), np.abs(my / (h - 1) - 0.5)) * 2
        if glare is not None:  # not the tile that has the light's reflection on it
            gl = cv2.resize(glare, (w, h), interpolation=cv2.INTER_LINEAR)
            central = central * (1 - 0.95 * cv2.remap(gl, mx, my, cv2.INTER_LINEAR))
        cover[f] = (u0, v0, inside, central)
    out = {}
    for k in range(1, n):
        x, y, bw, bh, area = stats[k]
        if area < max(20, 0.003 * tile_area):
            continue  # specks: sharp edges a fraction of a pixel apart, not a raised thing
        comp = lab[y:y + bh, x:x + bw] == k
        best, best_score = None, None
        for f, (u0, v0, inside, central) in cover.items():
            ys, xs = slice(y - v0, y - v0 + bh), slice(x - u0, x - u0 + bw)
            if y < v0 or x < u0 or y + bh > v0 + inside.shape[0] or x + bw > u0 + inside.shape[1]:
                sub_in = np.zeros_like(comp)
                sub_c = np.zeros(comp.shape, np.float32)
                yy0, xx0 = max(y, v0), max(x, u0)
                yy1, xx1 = min(y + bh, v0 + inside.shape[0]), min(x + bw, u0 + inside.shape[1])
                if yy1 <= yy0 or xx1 <= xx0:
                    continue
                sub_in[yy0 - y:yy1 - y, xx0 - x:xx1 - x] = inside[yy0 - v0:yy1 - v0, xx0 - u0:xx1 - u0]
                sub_c[yy0 - y:yy1 - y, xx0 - x:xx1 - x] = central[yy0 - v0:yy1 - v0, xx0 - u0:xx1 - u0]
            else:
                sub_in, sub_c = inside[ys, xs], central[ys, xs]
            seen = comp & sub_in
            if not seen.any():
                continue
            score = (round(seen.sum() / comp.sum() * 20) / 20, float(sub_c[seen].mean()))
            if best_score is None or score > best_score:
                best, best_score = f, score
        if best:
            out.setdefault(best, []).append((y, x, comp))
    return out


def overlap_gains(ts, maps, gain, q=4):
    """Per-tile colour gains (n x 3, in ts.tiles order) that make neighbours equally bright
    where they overlap: the median log ratio of the two tiles over their common pixels (not
    blown out, not dark) for every pair, then least squares over all tiles, each held near 1.
    Evens out what the flat field can't: the shiny plate reflecting differently from each
    position, light from outside."""
    files = [t["file"] for t in ts.tiles]
    idx = {f: k for k, f in enumerate(files)}
    sw, sh = ts.grays(1 / q)[files[0]].shape[::-1]
    g_small = None if gain is None else cv2.resize(gain, (sw, sh), interpolation=cv2.INTER_AREA)

    def small_colour(f):
        img = cv2.resize(ts.colour(f), (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32)
        return img if g_small is None else img * g_small
    colour = dict(zip(files, pmap(small_colour, files)))
    edges, rhs = [], []
    for f1, f2, _ in neighbour_pairs(ts):
        if f1 not in maps or f2 not in maps:
            continue
        got = pair_overlap(maps, f1, f2)
        if got is None:
            continue
        _, m1, m2 = got
        a = cv2.remap(colour[f1], *m1, cv2.INTER_LINEAR, borderValue=(-1, -1, -1))
        b = cv2.remap(colour[f2], *m2, cv2.INTER_LINEAR, borderValue=(-1, -1, -1))
        ok = (a.min(2) > 15) & (b.min(2) > 15) & (a.max(2) < CLIP - 5) & (b.max(2) < CLIP - 5)
        if ok.sum() < 50:
            continue
        edges.append((idx[f1], idx[f2]))
        rhs.append(np.median(np.log(a[ok]) - np.log(b[ok]), 0))  # log g2 - log g1
    if not edges:
        return None
    sol = solve_positions(len(files), edges, rhs, np.zeros((len(files), 3)), prior=0.3)
    return np.exp(sol - sol.mean(0))


def build_collage(ts, model, canvas, centres, overlap, blend="multiband", gain=None, glare=None,
                  tile_gains=None, whole=None, raised=None, levels=5):
    """Every tile drawn onto the canvas (X right, Y up) through the camera model, its centre at
    centres[file] = (cx, cy). Each canvas pixel belongs to the tile that shows it best
    (tile_quality: nearest its centre, but never from a blown-out part or the light's
    reflection when another tile saw it cleanly; a region in `whole` from its one tile).
    crop: each pixel straight from that tile (sharp, seams can show as brightness steps).
    multiband: the same choice, blended per frequency band (a Laplacian pyramid, `levels`
    deep): coarse light changes smoothly across a seam, fine detail switches within a pixel
    or two, so it stays sharp and the hole grid shows no ghosting.
    feather: all tiles mixed softly towards their edges (soft: ghosting where they disagree).
    gain: (h, w, 3) multiplied into every tile (flat field, colour); tile_gains: (n, 3) more
    per tile (overlap_gains). raised: raised_things() at 1/4 size: there multiband takes the
    plain choice instead (a seam through a big raised thing then shows as a step, not as two
    see-through copies).
    Memory per canvas pixel: crop 5 bytes, multiband ~28, feather 16."""
    h, w = ts.h, ts.w
    dx = np.minimum(np.arange(w) + 1, w - np.arange(w)).astype(np.float32)
    dy = np.minimum(np.arange(h) + 1, h - np.arange(h)).astype(np.float32)
    dist = np.minimum.outer(dy, dx)  # distance to the picture's nearest edge
    soft = blend == "feather"
    base = np.clip(dist / max(1.0, overlap * min(w, h)), 1e-3, 1.0) if soft else dist / dist.max()
    q = 4  # the scale of `whole`'s masks
    tiles = [t for t in ts.tiles if t["file"] in centres]
    index = {t["file"]: k for k, t in enumerate(ts.tiles)}
    acc = np.zeros((canvas.H, canvas.W, 3), np.float32 if soft else np.uint8) if blend != "multiband" else None
    wsum = np.zeros((canvas.H, canvas.W), np.float32 if soft else np.uint16)
    label = np.full((canvas.H, canvas.W), -1, np.int16) if blend == "multiband" else None

    def tile_image(t, box=None):
        got = canvas.tile_map(model, *centres[t["file"]]) if box is None else \
            canvas.tile_map_box(model, *centres[t["file"]], box)
        if got is None:
            return None
        img = ts.colour(t["file"])
        g = gain
        if tile_gains is not None:
            k = tile_gains[index[t["file"]]].astype(np.float32)
            g = (np.ones((h, w, 3), np.float32) if g is None else g) * k
        if g is not None:
            img = cv2.multiply(img, g, dtype=cv2.CV_8U)  # saturates, no float copy
        b, mx, my = got
        return b, mx, my, cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)

    def prepare(t):  # per tile, in parallel: its picture and quality on the canvas
        got = tile_image(t)
        if got is None:
            return None
        (u0, v0, u1, v1), mx, my, im = got
        weight = tile_quality(ts, t["file"], glare, base)
        # where this tile may be made to win a raised thing: outside the light's reflection
        # (blown-out white tape is blown out from every side: that doesn't count) and not at
        # its very edge (it fades into black there)
        clean = base > 0.03
        if glare is not None:
            clean &= cv2.resize(glare, (w, h), interpolation=cv2.INTER_LINEAR) < 0.4
        clean = clean.astype(np.uint8)
        if not soft:
            weight = (weight * 60000 + 1).astype(np.uint16)  # 0 stays "no tile here"
        wt = cv2.remap(weight, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        if not soft and whole and t["file"] in whole:  # its objects: this tile wins them
            box = np.zeros((v1 - v0, u1 - u0), bool)
            for y, x, comp in whole[t["file"]]:
                big = cv2.resize(comp.astype(np.uint8), (comp.shape[1] * q, comp.shape[0] * q),
                                 interpolation=cv2.INTER_NEAREST).astype(bool)
                Y, X = y * q - v0, x * q - u0
                ya, xa = max(0, Y), max(0, X)
                yb, xb = min(box.shape[0], Y + big.shape[0]), min(box.shape[1], X + big.shape[1])
                if yb > ya and xb > xa:
                    box[ya:yb, xa:xb] |= big[ya - Y:yb - Y, xa - X:xb - X]
            wt[box & (cv2.remap(clean, mx, my, cv2.INTER_NEAREST) > 0)] = 65535
        return (u0, v0, u1, v1), im, wt

    for k in range(0, len(tiles), 8):  # 8 at a time: in parallel, without all in memory
        for t, got in zip(tiles[k:k + 8], pmap(prepare, tiles[k:k + 8])):
            if got is None:
                continue  # cropped away
            (u0, v0, u1, v1), im, wt = got
            sw = wsum[v0:v1, u0:u1]
            if soft:
                acc[v0:v1, u0:u1] += im.astype(np.float32) * wt[..., None]
                sw += wt
                continue
            m = wt > sw
            np.copyto(sw, wt, where=m)
            if label is not None:
                label[v0:v1, u0:u1][m] = index[t["file"]]
            else:
                np.copyto(acc[v0:v1, u0:u1], im, where=m[..., None])
    if soft:
        acc /= np.maximum(wsum, 1e-6)[..., None]  # in place: no second float canvas
        return np.clip(acc, 0, 255, out=acc).astype(np.uint8)
    if label is None:
        return acc
    del wsum

    # multiband: Laplacian pyramids of the tiles, each level weighted by the blurred choice
    P = 2 ** levels
    Wp, Hp = math.ceil(canvas.W / P) * P, math.ceil(canvas.H / P) * P
    lab = np.full((Hp, Wp), -1, np.int16)
    lab[:canvas.H, :canvas.W] = label
    del label
    pyr = [np.zeros((Hp >> l, Wp >> l, 3), np.float32) for l in range(levels + 1)]
    plain = np.zeros((Hp, Wp, 3), np.uint8)  # the choice without blending, for `raised`
    pw = [np.zeros((Hp >> l, Wp >> l), np.float32) for l in range(levels + 1)]

    def bands(t):
        got = canvas.tile_map(model, *centres[t["file"]])
        if got is None:
            return None
        (u0, v0, u1, v1), _, _ = got  # the tile's box, widened to whole pyramid cells
        U0, V0 = max(0, (u0 // P - 1) * P), max(0, (v0 // P - 1) * P)
        U1, V1 = min(Wp, (u1 // P + 2) * P), min(Hp, (v1 // P + 2) * P)
        mask = (lab[V0:V1, U0:U1] == index[t["file"]]).astype(np.float32)
        if not mask.any():
            return None
        got = tile_image(t, (U0, V0, U1, V1))
        _, mx, my, im = got
        valid = cv2.remap(np.ones((h, w), np.float32), mx, my, cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT)
        gi, gv, gm = [im.astype(np.float32) * valid[..., None]], [valid], [mask]
        for _ in range(levels):
            gi.append(cv2.pyrDown(gi[-1]))
            gv.append(cv2.pyrDown(gv[-1]))
            gm.append(cv2.pyrDown(gm[-1]))
        # normalised: the picture carried on smoothly past its edge, so its edge doesn't
        # show up as a band of its own
        g = [a / np.maximum(v, 1e-3)[..., None] for a, v in zip(gi, gv)]
        lap = [g[l] - cv2.pyrUp(g[l + 1], dstsize=g[l].shape[1::-1]) for l in range(levels)] + [g[levels]]
        return (U0, V0), lap, gm, im

    for k in range(0, len(tiles), 4):  # 4 at a time: each holds a few pyramids
        for got in pmap(bands, tiles[k:k + 4]):
            if got is None:
                continue
            (U0, V0), lap, gm, im = got
            hh, ww = gm[0].shape
            np.copyto(plain[V0:V0 + hh, U0:U0 + ww], im, where=gm[0][..., None] > 0.5)
            for l in range(levels + 1):
                y0, x0 = V0 >> l, U0 >> l
                hh, ww = gm[l].shape
                pyr[l][y0:y0 + hh, x0:x0 + ww] += lap[l] * gm[l][..., None]
                pw[l][y0:y0 + hh, x0:x0 + ww] += gm[l]
    out = None
    for l in range(levels, -1, -1):
        band = pyr[l] / np.maximum(pw[l], 1e-6)[..., None]
        out = band if out is None else cv2.pyrUp(out, dstsize=band.shape[1::-1]) + band
        pyr[l] = pw[l] = None
    out = np.clip(out[:canvas.H, :canvas.W], 0, 255)
    if raised is not None and raised.any():
        r = cv2.resize(cv2.GaussianBlur(raised.astype(np.float32), (0, 0), 1.5),
                       (canvas.W, canvas.H), interpolation=cv2.INTER_LINEAR)[..., None]
        out = out * (1 - r) + plain[:canvas.H, :canvas.W] * r
    out = out.astype(np.uint8)
    out[lab[:canvas.H, :canvas.W] < 0] = 0  # no tile there
    return out


def solve_positions(n, edges, rhs, cmd, prior):
    """The values x (n x k: tile centres, or log colour gains) that best fit x[j] - x[i] = d for
    every pair (i, j, d) and x = cmd (weighted by prior): the normal equations (graph Laplacian + prior^2 I) solved by
    conjugate gradients. Sparse: memory and time grow with the pairs, not with n^2 as the
    dense least squares did (thousands of tiles are fine)."""
    i, j = np.array(edges).T
    d = np.array(rhs, float)
    p2 = prior * prior

    def apply(x):
        diff = x[j] - x[i]
        out = p2 * x
        np.add.at(out, j, diff)
        np.add.at(out, i, -diff)
        return out
    b = p2 * cmd
    np.add.at(b, j, d)
    np.add.at(b, i, -d)
    x = cmd.copy()
    r = b - apply(x)
    p, rs = r.copy(), (r * r).sum(0)
    tol = 1e-24 * max(1.0, float((b * b).sum()))  # residual 1e-12 of b: exact to float
    for _ in range(10 * n + 100):
        if rs.max() <= tol:
            break
        Ap = apply(p)
        pAp = (p * Ap).sum(0)
        alpha = np.divide(rs, pAp, out=np.zeros(len(rs)), where=pAp > 0)
        x += alpha * p
        r -= alpha * Ap
        new = (r * r).sum(0)
        p = r + np.divide(new, rs, out=np.zeros(len(rs)), where=rs > 0) * p
        rs = new
    return x


def refine_positions(ts, B, raw=None, min_strength=0.12):
    """Where each tile really was: the head isn't exactly where it was sent (backlash, a loose
    camera, skipped steps). Every neighbour pair whose overlap matches well says how far apart
    the two pictures are on the bed; least squares finds the tile positions that agree best
    with all pairs, staying near the commanded positions where no pair says otherwise.
    raw: fit_b's last pair_shifts (the same pairs, measured with almost this B), else they are
    measured here. Returns ({file: (cx, cy)}, [(file1, file2, mm the pair still disagrees)])
    or (None, []), and prints the largest corrections."""
    raw = pair_shifts(ts, B) if raw is None else raw
    files = [t["file"] for t in ts.tiles]
    idx = {f: k for k, f in enumerate(files)}
    edges, rhs = [], []
    for f1, f2, dh, sh, strength in raw:
        if strength < min_strength:
            continue
        d = -B @ sh  # how far apart the pictures are on the bed
        if np.hypot(*(d - dh)) > 0.25 * np.hypot(*dh):
            continue  # implausible: a wrong match
        edges.append((idx[f1], idx[f2]))
        rhs.append(d)
    if not edges:
        return None, []
    cmd = np.array([[t["cx"], t["cy"]] for t in ts.tiles], float)
    sol = solve_positions(len(files), edges, rhs, cmd, prior=0.3)  # a pair ~3x as sure
    corr = np.hypot(*(sol - cmd).T)
    k = int(np.argmax(corr))
    print(f"tile positions from {len(edges)} pairs: corrected by up to {corr[k]:.2f} mm "
          f"({files[k]})" + ("  <- more than 2 mm: loose camera or skipped steps?"
                             if corr[k] > 2 else ""))
    left = [(files[i], files[j], float(np.hypot(*(sol[j] - sol[i] - d))))
            for (i, j), d in zip(edges, rhs)]
    return {f: (float(sol[k][0]), float(sol[k][1])) for k, f in enumerate(files)}, left


def measure_mapping(folder, ts, meta, B0, a):
    """The mapping B for this scan, measured from its tile overlaps. Starting points: the
    calibration (B0), then, if that isn't confirmed (camera moved, turned, raised), the hole
    grid: its spacing gives the scale, its angle the turn up to 90 deg, and the four 90 deg
    choices are tried. A result counts only if the tiles agree (rms <= 8 px) and its scale
    matches the hole grid (10 %) or, without one, the calibration (25 %). A trusted result is
    saved in calibration.json with the hole grid as the reference for the next remount.
    The starts are tried on half-size pictures when the overlaps are wide enough (4x fewer
    pixels), the winner is then measured on the full ones. Returns (B, the pair shifts of the
    full-size fit or None)."""
    cal = load_json(CALIB)
    same_cam = cal.get("image_size") == meta.get("image_size")
    ref = cal.get("lattice") if same_cam else None
    lat = scan_lattice(ts)
    s_lat = ref["pitch_mm"] / lat[0] if ref and lat else None
    mmpp0 = b_describe(B0)[0]

    def trusted(fit):
        s = b_describe(fit[0])[0]
        return fit[2] <= 8 and (abs(s / s_lat - 1) <= 0.10 if s_lat else abs(s / mmpp0 - 1) <= 0.25)

    tries = [("calibration", B0)]
    if s_lat:
        Bref = np.array(ref["B"])
        d = lat[1] - ref["angle"]
        tries += [(f"hole grid, turned {sg * d + 90 * k:+.0f} deg",
                   s_lat / b_describe(Bref)[0] * rot2(sg * d + 90 * k) @ Bref)
                  for k in range(4) for sg in (1, -1)]
        if abs(s_lat / mmpp0 - 1) > 0.05:
            print(f"hole grid: {s_lat:.4f} mm/px, calibration {mmpp0:.4f}: the camera was moved")
    coarse = coarse_scale(ts, B0)
    best, best_scale = None, 1.0
    for scale in sorted({coarse, 1.0}):  # half size first; the full size only if that fails
        for label, Bc in tries:
            fit = fit_b(ts, Bc, scale)
            if fit and trusted(fit) and (best is None or (fit[1], -fit[2]) > (best[1], -best[2])):
                best, best_scale = (*fit, label), scale
            if best and label == "calibration":
                break  # nothing moved: done
        if best:
            break
    if best and best_scale != 1.0:  # the winner, measured on the full pictures
        full = fit_b(ts, best[0], 1.0, passes=1)
        best = (*full, best[4]) if full and trusted(full) else (*best[:3], None, best[4])
    if not best:
        print("tile overlaps too plain or too few to measure" +
              (f" (hole grid says {s_lat:.4f} mm/px)" if s_lat else ""))
        override = any(getattr(a, k) is not None for k in ("mm_per_px", "rot", "flip_x", "flip_y"))
        if cal.get("B") and not override and same_cam and not (
                s_lat and abs(s_lat / b_describe(np.array(cal["B"]))[0] - 1) > 0.10):
            print(f"placed by the mapping measured on {cal.get('B_from', 'an earlier scan')}")
            return np.array(cal["B"]), None
        print("placed by the calibration only" + (": the camera moved since, run --check or "
              "scan a plate with some marks on it" if s_lat else ""))
        return B0, None
    B, n, rms, raw, label = best
    mmpp, ang, mir = b_describe(B)
    rot, flip_x = nearest_params(B)
    turn = (ang - b_describe(b_from_params({"mm_per_px": 1, "rot": rot, "flip_x": flip_x,
                                            "flip_y": False}))[1] + 180) % 360 - 180
    print(f"measured from {n} tile overlaps (rms {rms:.1f} px, start: {label}): {mmpp:.4f} mm/px,"
          f" camera at rot {rot}{' + mirror' if flip_x else ''} {turn:+.1f} deg")
    meta.update(fit={"B": B.tolist(), "pairs": n, "rms_px": round(rms, 2)},
                rot=rot, flip_x=flip_x, flip_y=False)  # tiles shown upright from now on
    save_json(folder / "tiles.json", meta)
    created = meta.get("created", "")
    # only a clean fit becomes the reference, and an older scan rebuilt doesn't win
    if n >= 4 and rms <= 4 and same_cam and created >= cal.get("B_created", ""):
        new = {**cal, "mm_per_px": round(mmpp, 5), "rot": rot, "flip_x": flip_x, "flip_y": False,
               "B": B.tolist(), "B_from": str(folder), "B_created": created}
        if lat:
            new["lattice"] = {"pitch_mm": ref["pitch_mm"] if ref else round(lat[0] * mmpp, 4),
                              "angle": round(lat[1], 2), "B": B.tolist()}
        save_json(CALIB, new)
        print(f"  saved in {CALIB}: the next scan plans its grid with it")
    return B, raw


def camera_fit(folder, ts, meta, B):
    """fit_camera for collage(): (model, {file: centre}, pairs left) or (None, None, []) when
    it can't be trusted (too few or too plain overlaps, or the tiles still disagree by more
    than 3 px). Prints what it found; a clean fit is kept in tiles.json and calibration.json
    (the camera's tilt and lens stay the same until it is moved)."""
    cmd = {t["file"]: (t["cx"], t["cy"]) for t in ts.tiles}
    got = fit_camera(ts, B, cmd)
    if got is None or got[3]["rms_px"] > 3:
        print("camera model: overlaps too plain or inconsistent" +
              (f" (rms {got[3]['rms_px']:.1f} px)" if got else "") + ": scale + rotation only")
        return None, None, []
    model, pos, left, info = got
    lens, keystone = model.describe()
    corr = max(np.hypot(*np.subtract(pos[f], cmd[f])) for f in cmd)
    s = b_describe(B)[0]
    print(f"camera model from {info['points']} points in {info['pairs']} overlaps: rms "
          f"{info['rms_px']:.2f} px; lens {lens:+.1f} % at the corners, keystone {keystone:.1f} %, "
          f"{math.sqrt(abs(np.linalg.det(model.b()))):.4f} mm/px (scale + rotation said {s:.4f}); "
          f"tiles moved up to {corr:.2f} mm" + ("  <- more than 2 mm: loose camera or skipped "
                                                   "steps?" if corr > 2 else ""))
    meta["camera_model"] = {**model.to_json(), **info}
    save_json(folder / "tiles.json", meta)
    cal = load_json(CALIB)
    if info["rms_px"] <= 2 and cal.get("image_size") == meta.get("image_size") and             meta.get("created", "") >= cal.get("camera_model_created", ""):
        save_json(CALIB, {**cal, "camera_model": model.to_json(),
                          "camera_model_created": meta.get("created", "")})
    return model, pos, left


def camera_offset(a, meta):
    """(the camera offset to place the tiles with, the one the scan was planned with): the
    command line, else calibration.json's (--offset-calibrate), else the scan's own. A scan
    taken before the offset was measured is simply moved by the difference."""
    used = tuple(meta.get("cam_offset") or (0.0, 0.0))
    if a.cam_offset is not None:
        return tuple(a.cam_offset), used
    cal = load_json(CALIB).get("cam_offset")
    return (tuple(cal) if cal else used), used


def collage(folder, meta, a):
    """Build collage.jpg (or .png when too big for JPEG), collage_seams.jpg and collage.json
    (where the picture lies on the bed: pixel -> head mm) from a scan's tiles."""
    for key in ("mm_per_px", "rot", "flip_x", "flip_y"):  # the command line wins
        if getattr(a, key) is not None:
            meta[key] = getattr(a, key)
    if a.undistort:
        meta["lens"] = load_json(a.undistort) or sys.exit(f"no lens calibration in {a.undistort}")
    t0 = time.monotonic()
    ts = TileSet(folder, meta)  # every tile read once, for all the steps below
    B, raw, pos, left = b_from_params(meta), None, None, []
    model = None
    if not a.no_fit:
        B, raw = measure_mapping(folder, ts, meta, B, a)
        model, pos, left = camera_fit(folder, ts, meta, B)
        if model is None:  # too plain to fit the camera: scale + rotation, one shift a pair
            pos, left = refine_positions(ts, B, raw)
    model = model or CameraModel.from_b(B, (ts.w, ts.h))
    off, used = camera_offset(a, meta)
    shift = np.subtract(off, used)
    if np.abs(shift).max() > 1e-6:
        print(f"camera offset {off[0]:g}, {off[1]:g} mm (the scan was planned with {used[0]:g}, "
              f"{used[1]:g}): collage moved by {shift[0]:+.2f}, {shift[1]:+.2f} mm")
    centres = {t["file"]: tuple(np.add(pos[t["file"]] if pos and t["file"] in pos
                                       else (t["cx"], t["cy"]), shift)) for t in ts.tiles}
    bounds = None
    if a.crop_travel:
        if meta.get("max"):
            bounds = (0.0, 0.0, float(meta["max"][0]), float(meta["max"][1]))
        else:
            print("this scan has no travel saved (taken before it was): not cropped")
    canvas = plan_canvas(ts, model, centres, a.res, bounds)

    flat = None if a.no_flat else flat_field(ts)
    wb = None if a.no_wb else colour_gains(ts)
    gain = None
    if flat is not None or wb is not None:
        f = 1.0 / np.maximum(flat, 0.2) if flat is not None else np.ones((ts.h, ts.w), np.float32)
        k = wb if wb is not None else np.ones(3)
        gain = cv2.merge([(f * k[c]).astype(np.float32) for c in range(3)])
    if wb is not None:
        print(f"colour: gains B {wb[0]:.2f} G {wb[1]:.2f} R {wb[2]:.2f} (grey made grey, "
              "brightest just below white)")
    glare = None if a.no_glare else glare_map(ts)
    if glare is not None:
        print(f"light's reflection: {100 * (glare > 0.5).mean():.0f} % of each picture, "
              "taken from neighbouring tiles where they saw it cleanly")
    maps = overlap_maps(ts, model, canvas, centres)
    err, seen, detail = disagreement(ts, maps, canvas)
    whole = None if a.blend == "feather" or a.no_whole else keep_objects_whole(ts, maps, err, seen, glare)
    if whole:
        print(f"{sum(len(v) for v in whole.values())} raised things (pliers, tape...) each taken "
              "whole from one tile, the seams go round them")
    tile_gains = None if a.no_gain else overlap_gains(ts, maps, gain)
    if tile_gains is not None:
        print(f"tile brightness evened from the overlaps: gains {tile_gains.min():.2f}..{tile_gains.max():.2f}")
    blend = a.blend
    if blend == "multiband" and canvas.W * canvas.H > 60e6:
        print("canvas over 60 Mpx: --blend crop (multiband needs ~25 bytes a pixel)")
        blend = "crop"
    img = build_collage(ts, model, canvas, centres, meta["overlap"], blend, gain, glare,
                        tile_gains, whole, None if a.no_whole else raised_things(err, seen))

    if max(img.shape[:2]) > JPEG_MAX:
        out = folder / "collage.png"
        print(f"collage wider or taller than JPEG allows ({JPEG_MAX} px): saved as PNG")
        cv2.imwrite(str(out), img, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    else:
        out = folder / "collage.jpg"
        cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    s = canvas.s
    print(f"collage {img.shape[1]}x{img.shape[0]} px ({img.shape[1] * s:.0f} x "
          f"{img.shape[0] * s:.0f} mm, {s:.4f} mm/px) -> {out}  ({time.monotonic() - t0:.1f} s)")

    seams_pic = seam_picture(err, seen, img)
    cv2.imwrite(str(folder / "collage_seams.jpg"), seams_pic, [cv2.IMWRITE_JPEG_QUALITY, 85])
    left_mm = {(f1, f2): r for f1, f2, r in left}
    pairs = sorted(({"a": f1, "b": f2, "detail_diff": round(d, 1),
                     "left_mm": round(left_mm[(f1, f2)], 3) if (f1, f2) in left_mm else None}
                    for (f1, f2), d in detail.items()), key=lambda p: -p["detail_diff"])
    if pairs:
        print(f"seams: detail difference median {np.median([p['detail_diff'] for p in pairs]):.1f}, "
              f"worst {pairs[0]['a']} / {pairs[0]['b']} {pairs[0]['detail_diff']:.1f} "
              f"-> {folder / 'collage_seams.jpg'}")
    save_json(folder / "collage.json", {
        "image": out.name, **canvas.describe(),
        "frame": "pixel (u, v) centre = head mm (x0 + u * mm_per_px, y1 - v * mm_per_px), from "
                 "the home switches (the bridge's mpos); with cam_offset measured, the point "
                 "under the tool at that head position",
        "cam_offset": [round(v, 3) for v in off], "cropped_to_travel": bounds is not None,
        "colour_gains": None if wb is None else [round(float(v), 4) for v in wb],
        "glare": glare is not None, "blend": blend, "fit": meta.get("fit"),
        "camera_model": model.to_json(), "pairs": pairs,
        "tiles": {f: [round(float(c[0]), 3), round(float(c[1]), 3)] for f, c in centres.items()},
        "created": datetime.now().isoformat(timespec="seconds")})

    if a.stitch:
        imgs = [orient(ts.colour(t["file"]), meta["flip_x"], meta["flip_y"], meta["rot"])
                for t in ts.tiles]
        status, pano = cv2.Stitcher_create(cv2.Stitcher_SCANS).stitch(imgs)
        if status == cv2.Stitcher_OK:
            cv2.imwrite(str(folder / "collage_stitched.jpg"), pano)
            print(f"stitched {pano.shape[1]}x{pano.shape[0]} -> {folder / 'collage_stitched.jpg'}")
        else:
            print(f"Stitcher failed (status {status}: too few matching features?). "
                  "collage.jpg (placed by X/Y) is still there")
    return img


class Viewer:
    """--show: a window with one cell per tile, laid out like the bed (columns X, rows Y with
    +Y up), filling in as the photos are taken; then the collage. Saved as contact.jpg."""

    def __init__(self, plan, meta, folder):
        self.plan, self.meta, self.folder = plan, meta, folder
        self.rows = max(p["row"] for p in plan) + 1
        self.cols = max(p["col"] for p in plan) + 1
        w, h = meta["image_size"]
        if meta["rot"] in (90, 270):
            w, h = h, w
        self.cw = min(360, (1400 - 6) // self.cols - 6)
        self.ch = round(self.cw * h / w)
        self.top = 40
        W = 6 + self.cols * (self.cw + 6)
        H = self.top + 6 + self.rows * (self.ch + 6)
        self.base = np.full((H, W, 3), 40, np.uint8)
        self.order = {(p["row"], p["col"]): i + 1 for i, p in enumerate(plan)}
        for (r, c), k in self.order.items():  # empty cells: the order the head takes
            x, y = self.cell(r, c)
            cv2.rectangle(self.base, (x, y), (x + self.cw, y + self.ch), (70, 70, 70), -1)
            cv2.putText(self.base, f"#{k}", (x + self.cw // 2 - 20, y + self.ch // 2 + 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (130, 130, 130), 2)
        self.status, self.moving, self.last = "", None, None
        cv2.namedWindow("scan", cv2.WINDOW_NORMAL)
        f = min(1.0, 1400 / W, 900 / H)
        cv2.resizeWindow("scan", int(W * f), int(H * f))
        self.draw()

    def cell(self, r, c):
        return 6 + c * (self.cw + 6), self.top + 6 + (self.rows - 1 - r) * (self.ch + 6)

    def draw(self):
        img = self.base.copy()
        cv2.putText(img, self.status, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        for key, colour in ((self.last, (0, 200, 0)), (self.moving, (0, 220, 255))):
            if key:
                x, y = self.cell(*key)
                cv2.rectangle(img, (x - 3, y - 3), (x + self.cw + 2, y + self.ch + 2), colour, 3)
        cv2.imshow("scan", img)
        cv2.waitKey(1)
        return img

    def pump(self):
        cv2.waitKey(1)

    def going(self, p):
        self.moving = (p["row"], p["col"])
        self.status = (f"#{self.order[self.moving]}/{len(self.plan)}: moving to row {p['row']} "
                       f"col {p['col']}  X{p['x']:.1f} Y{p['y']:.1f}")
        self.draw()

    def tile(self, p, jpg):
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        img = orient(img, self.meta["flip_x"], self.meta["flip_y"], self.meta["rot"])
        img = cv2.resize(img, (self.cw, self.ch), interpolation=cv2.INTER_AREA)
        x, y = self.cell(p["row"], p["col"])
        self.base[y:y + self.ch, x:x + self.cw] = img
        label = f"r{p['row']} c{p['col']}  X{p['x']:.0f} Y{p['y']:.0f}"
        for colour, thick in (((0, 0, 0), 3), ((255, 255, 255), 1)):
            cv2.putText(self.base, label, (x + 5, y + self.ch - 8), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, colour, thick)
        self.last, self.moving = (p["row"], p["col"]), None
        self.status = f"#{self.order[self.last]}/{len(self.plan)}: row {p['row']} col {p['col']} taken"
        self.draw()

    def finish(self, collage_img):
        self.moving = None
        self.status = "done: collage in the other window. Any key closes."
        cv2.imwrite(str(self.folder / "contact.jpg"), self.draw())
        if collage_img is not None:
            cv2.namedWindow("collage", cv2.WINDOW_NORMAL)
            h, w = collage_img.shape[:2]
            f = min(1.0, 1000 / w, 900 / h)
            cv2.resizeWindow("collage", int(w * f), int(h * f))
            cv2.imshow("collage", collage_img)
        while cv2.waitKey(100) == -1:
            if all(cv2.getWindowProperty(n, cv2.WND_PROP_VISIBLE) < 1
                   for n in ("scan", "collage") if collage_img is not None or n == "scan"):
                break
        cv2.destroyAllWindows()


# ---------------------------------------------------------------- modes
def setup_bridge(a, need_homed=True):
    br = Bridge(a.bridge, a.token)
    try:
        st, sv = br.state(), br.settings()
    except (OSError, ValueError) as e:
        sys.exit(f"bridge {a.bridge} not reachable: {e}")
    if st["link"] != "up" or st["stopped"] or st["halted"] or st["homing"]:
        sys.exit(f"bridge not ready: link {st['link']}, stopped {st['stopped']}, "
                 f"halted {st['halted']}, homing {st['homing']}")
    if need_homed and not (st["homed"][0] and st["homed"][1]):
        sys.exit("X/Y not homed: press Home XY in the page first (G28 is refused)")
    return br, st, sv


def mode_check(a, cam):
    """Move +X then +Y by a.check_mm and measure how the picture shifts."""
    br, st, sv = setup_bridge(a)
    feed = a.feed or sv["jog_f_xy"]
    x0, y0 = st["mpos"][0], st["mpos"][1]
    d = a.check_mm
    if x0 + d > st["max"][0] or y0 + d > st["max"][1]:
        sys.exit("too near the limit: move the head inward first")
    gray = lambda im: np.float32(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY))
    print(f"head at X{x0:g} Y{y0:g}; moving {d:g} mm in X, then in Y, then back")
    st = br.move_to(x0, y0, feed, st)
    g0 = gray(cam.image(a.settle, still=True))
    win = cv2.createHanningWindow(g0.shape[::-1], cv2.CV_32F)
    (bx, by), _ = cv2.phaseCorrelate(g0, gray(cam.image(a.settle, still=True)), win)  # noise, no move
    noise = math.hypot(bx, by)
    print(f"  no move: {noise:.1f} px (noise)")
    shifts = {}
    for ax, (x, y) in (("X", (x0 + d, y0)), ("Y", (x0, y0 + d))):
        st = br.move_to(x, y, feed, st)
        (sx, sy), resp = cv2.phaseCorrelate(g0, gray(cam.image(a.settle, still=True)), win)
        shifts[ax] = (sx, sy, resp)
        print(f"  +{d:g} mm {ax}: picture moved {sx:+.1f}, {sy:+.1f} px (match {resp:.2f})")
        st = br.move_to(x0, y0, feed, st)
    mx, my = math.hypot(*shifts["X"][:2]), math.hypot(*shifts["Y"][:2])
    least = max(3.0, 3 * noise)
    if mx < least and my < least:
        print("The picture did not move: the camera is FIXED (not on the head), or it sees "
              "only something that moves with the head. Tiling needs a camera on the head.")
        return
    # a camera on the head: both moves shift the picture, by the same amount, at right angles
    cos = abs(np.dot(shifts["X"][:2], shifts["Y"][:2])) / (mx * my + 1e-9)
    if min(mx, my) < least or not 0.7 < mx / my < 1.4 or cos > 0.35:
        print("Unclear: the shifts are not the same size at right angles (something moving "
              "in view, a plain or regular plate?). Try again, a larger --check-mm, or "
              "--calibrate with a ruler. Nothing saved.")
        return
    # which mirror/rotation makes +X go right and +Y go up: the scene then moves left / down
    best = max(((fx, r) for fx in (False, True) for r in (0, 90, 180, 270)),
               key=lambda o: (-orient_vec(shifts["X"][:2], *o)[0]
                              + orient_vec(shifts["Y"][:2], *o)[1]))
    mmpp = 2 * d / (mx + my)
    print(f"The camera moves with the head. {mmpp:.4f} mm/px "
          f"(X {d / mx:.4f}, Y {d / my:.4f}: these should be close)")
    print(f"orientation: rot {best[1]}{', flip-x' if best[0] else ''}")
    if min(shifts["X"][2], shifts["Y"][2]) < 0.1:
        print("weak match (plain or regular plate?): check with --calibrate (ruler)")
    h, w = g0.shape
    save_json(CALIB, {"mm_per_px": round(mmpp, 5), "rot": best[1], "flip_x": best[0],
                      "flip_y": False, "image_size": [w, h], "from": "check",
                      "date": datetime.now().isoformat(timespec="seconds")})
    print(f"saved in {CALIB}")


def click_points(img, n, prompt):
    """A window with the picture: the user clicks n points (a click again moves the last one),
    then presses a key. Returns [(x, y)] in picture pixels."""
    pts = []
    view = img.copy()

    def click(ev, x, y, *_):
        nonlocal view
        if ev == cv2.EVENT_LBUTTONDOWN:
            if len(pts) == n:
                pts.pop()
            pts.append((x, y))
            view = img.copy()
            for p in pts:
                cv2.drawMarker(view, p, (0, 0, 255), cv2.MARKER_CROSS, 21, 1)
            if len(pts) == 2:
                cv2.line(view, pts[0], pts[1], (0, 0, 255), 1)
    cv2.namedWindow("calibrate", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("calibrate", click)
    print(prompt)
    while True:
        cv2.imshow("calibrate", view)
        if cv2.waitKey(50) != -1 and len(pts) == n:
            break
        if cv2.getWindowProperty("calibrate", cv2.WND_PROP_VISIBLE) < 1:
            sys.exit("window closed, nothing saved")
    cv2.destroyAllWindows()
    return pts


def mode_offset(a, cam):
    """Where the camera looks relative to the tool (laser spot or nozzle), so the collage's mm
    are where the tool goes: with the tool, make a small mark at head X,Y (--offset-calibrate
    X,Y; the laser: one short low-power pulse, the nozzle: a pen dot on paper; this script
    fires nothing), jog the head in the page until the camera sees the mark, then click it.
    offset = mark - head now - (where the click is, from the picture centre, in mm)."""
    br, st, _ = setup_bridge(a)
    head = np.array(st["mpos"][:2], float)
    mark = np.array(a.offset_calibrate, float)
    cal = load_json(CALIB)
    if not cal.get("mm_per_px"):
        sys.exit("mm per pixel unknown: --check or --calibrate first")
    B = np.array(cal["B"]) if cal.get("B") else b_from_params(
        {"mm_per_px": cal["mm_per_px"], "rot": cal.get("rot", 0),
         "flip_x": cal.get("flip_x", False), "flip_y": cal.get("flip_y", False)})
    img = cam.image(0.3)
    h, w = img.shape[:2]
    if cal.get("image_size") and list(cal["image_size"]) != [w, h]:
        sys.exit(f"calibrated at {cal['image_size'][0]}x{cal['image_size'][1]}, camera gives "
                 f"{w}x{h}: use the same --size in usb_cam.py")
    (px, py), = click_points(img, 1, f"Head at X{head[0]:g} Y{head[1]:g}. Click the centre of the "
                                     "mark, then press a key in the picture window.")
    model = CameraModel.from_json(cal["camera_model"]) if cal.get("camera_model") and \
        list(cal["camera_model"]["size"]) == [w, h] else CameraModel.from_b(B, (w, h))
    seen = model.to_bed(np.array([px, py], float))  # the mark from the picture centre, mm
    off = mark - head - seen
    print(f"mark at X{mark[0]:g} Y{mark[1]:g}, head now X{head[0]:g} Y{head[1]:g}, mark "
          f"{seen[0]:+.2f}, {seen[1]:+.2f} mm from the picture centre")
    print(f"camera offset: {off[0]:+.2f}, {off[1]:+.2f} mm (the picture centre from the tool)")
    if np.hypot(*off) > 100:
        print("  more than 100 mm: right mark, right X,Y? Saved anyway; run it again to check")
    save_json(CALIB, {**load_json(CALIB), "cam_offset": [round(float(v), 3) for v in off],
                      "cam_offset_date": datetime.now().isoformat(timespec="seconds")})
    print(f"saved in {CALIB}: scans and collages use it from now on (also older scans, "
          "with --collage-only)")


def mode_calibrate(a, cam):
    """Click two points on a ruler in the photo, then type the mm between them.
    With a camera model from a scan (calibration.json), the distance is measured through it, so
    the camera's tilt and lens don't matter (where in the picture the ruler lies), and the ruler
    is compared with the head moves the model was measured with: they should agree. If they
    don't, the axes move more or less than the G-code says (steps/mm), or the camera was moved
    since that scan. Collages keep using the head moves (the laser goes by G-code mm); the
    ruler's mm/px only plans the next scan's grid (the scan measures the rest)."""
    img = cam.image(0.3)
    h, w = img.shape[:2]
    cal = load_json(CALIB)
    model = CameraModel.from_json(cal["camera_model"]) if cal.get("camera_model") and \
        list(cal["camera_model"]["size"]) == [w, h] else None
    pts = click_points(img, 2, "Click two marks on the ruler (far apart, the window can be made "
                               "bigger), then press a key in the picture window.")
    px = math.dist(*pts)
    mm = float(input(f"{px:.1f} px between the points. How many mm is that? "))
    if model is None:
        mmpp = mm / px
        print("no camera model yet (scan once first): the plain pixel distance is used, which "
              "the camera's tilt can put a few % off unless the ruler crosses the picture centre")
    else:
        seen = float(np.hypot(*(model.to_bed(np.array(pts[1], float)) -
                                model.to_bed(np.array(pts[0], float)))))
        k = mm / seen
        mmpp = math.sqrt(abs(np.linalg.det(model.b()))) * k  # at the picture centre
        print(f"through the camera model (tilt and lens taken out) the head moves say {seen:.2f} mm,"
              f" the ruler {mm:g} mm: {100 * (k - 1):+.2f} %")
        if abs(k - 1) > 0.01:
            print("  more than 1 %: the axes move more or less than the G-code says (steps/mm), or "
                  "the camera was moved since the scan the model is from. Collages keep the "
                  "G-code mm (where the tool goes); the next scan measures the camera again.")
        else:
            print("  within 1 %: the axes move what the G-code says")
    old = {k: v for k, v in cal.items() if k not in ("B", "B_from")}  # the camera may have moved
    save_json(CALIB, {**old, "mm_per_px": round(mmpp, 5), "image_size": [w, h],
                      "from": "ruler", "date": datetime.now().isoformat(timespec="seconds")})
    print(f"{mmpp:.4f} mm/px at the picture centre, field of view about {w * mmpp:.1f} x "
          f"{h * mmpp:.1f} mm -> {CALIB}")


def mode_lens(a, cam):
    """Checkerboard photos (move the board between them) -> camera matrix + distortion."""
    cols, rows = (int(v) for v in a.board.lower().split("x"))
    obj = np.zeros((rows * cols, 3), np.float32)
    obj[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    objs, imgs = [], []
    print(f"Show a {cols}x{rows} (inner corners) checkerboard in different places and angles.")
    while len(imgs) < a.lens_shots:
        g = cv2.cvtColor(cam.image(1.0), cv2.COLOR_BGR2GRAY)
        ok, corners = cv2.findChessboardCorners(g, (cols, rows))
        if ok:
            corners = cv2.cornerSubPix(g, corners, (11, 11), (-1, -1),
                                       (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3))
            objs.append(obj)
            imgs.append(corners)
            print(f"  {len(imgs)}/{a.lens_shots}: board found, move it")
        else:
            print("  no board in the picture")
    err, K, dist, _, _ = cv2.calibrateCamera(objs, imgs, g.shape[::-1], None, None)
    save_json(LENS, {"K": K.tolist(), "dist": dist.tolist(), "image_size": list(g.shape[::-1]),
                     "rms_px": err})
    print(f"lens calibration (error {err:.2f} px) -> {LENS}; use it with --undistort")


def latest_scan():
    dirs = sorted(p for p in SCANS.glob("*") if (p / "tiles.json").exists())
    return dirs[-1] if dirs else None


def mode_scan(a, cam):
    folder = Path(a.out) if a.out else None
    if a.resume:
        folder = folder or latest_scan()
        if not folder or not (folder / "tiles.json").exists():
            sys.exit("nothing to resume (no scans/*/tiles.json)")
        meta = load_meta(folder)
        print(f"resuming {folder}: the grid and settings saved there are used")
    else:
        folder = folder or SCANS / datetime.now().strftime("%Y-%m-%d_%H%M")
        calib = load_json(CALIB)
        mmpp = a.mm_per_px or calib.get("mm_per_px")
        if not mmpp:
            sys.exit("mm per pixel unknown: --mm-per-px, --check or --calibrate first")
        meta = {"mm_per_px": mmpp, "overlap": a.overlap,
                "rot": a.rot if a.rot is not None else calib.get("rot", 0),
                "flip_x": a.flip_x if a.flip_x is not None else calib.get("flip_x", False),
                "flip_y": a.flip_y if a.flip_y is not None else calib.get("flip_y", False),
                "cam_offset": list(a.cam_offset if a.cam_offset is not None
                                   else calib.get("cam_offset", (0.0, 0.0))), "lens": None}
        if a.undistort:
            meta["lens"] = load_json(a.undistort) or sys.exit(f"no lens calibration in {a.undistort}")

    # bridge (in a dry run only if it answers)
    if a.dry_run:
        try:
            br, st, sv = setup_bridge(a, need_homed=False)
        except SystemExit as e:
            print(f"{e} -> dry run with max 170 mm, feed 3000")
            br, st, sv = None, {"max": [170, 170, 170], "mpos": [None] * 3}, {"jog_f_xy": 3000}
    else:
        br, st, sv = setup_bridge(a)
    feed = a.feed or sv["jog_f_xy"]

    if "tiles" not in meta:  # a new grid
        raw = None
        try:
            raw = cam.image()
            size = raw.shape[1], raw.shape[0]
        except (OSError, RuntimeError):
            if not a.dry_run:
                sys.exit(f"camera {a.camera} not reachable: start tools/usb_cam.py")
            size = tuple(load_json(CALIB).get("image_size", (640, 480)))
            print(f"camera not reachable -> dry run with a {size[0]}x{size[1]} picture")
        cal_size = load_json(CALIB).get("image_size")
        if not a.mm_per_px and cal_size and list(cal_size) != list(size):
            sys.exit(f"calibrated at {cal_size[0]}x{cal_size[1]}, camera gives {size[0]}x{size[1]}:"
                     " calibrate again or use the same --size in usb_cam.py")
        w, h = (size[1], size[0]) if meta["rot"] in (90, 270) else size
        cal = load_json(CALIB)
        ref = cal.get("lattice")
        pl = picture_lattice(raw) if ref and not a.mm_per_px and raw is not None else None
        if pl:  # camera moved since the calibration? the hole grid tells, before any move
            s_live = ref["pitch_mm"] / pl[0]
            dang = (pl[1] - ref["angle"] + 45) % 90 - 45
            if abs(s_live / meta["mm_per_px"] - 1) > 0.05 or abs(dang) > 3:
                print(f"camera moved since the calibration: now {s_live:.4f} mm/px (was "
                      f"{meta['mm_per_px']:.4f}), turned {dang:+.1f} deg (+ 90s unknown): grid "
                      "planned with a square field, the collage measures the rest")
                meta["mm_per_px"] = s_live
                w = h = min(size)  # the turn may be 90 deg: plan for both
        fov = (w * meta["mm_per_px"], h * meta["mm_per_px"])
        off = meta["cam_offset"]
        # default: the head goes over its whole travel (0..max), so the pictures reach as far
        # as the machine can, wherever the camera sits on the head. (Stopping half a picture
        # inside each end assumed the camera looks straight at the nozzle: with the camera
        # beside it, the left and front strips were never photographed.)
        # 1 mm off each end: 0 is where the home switch triggers
        area = a.area or (off[0] - fov[0] / 2 + 1, off[1] - fov[1] / 2 + 1,
                          st["max"][0] + off[0] + fov[0] / 2 - 1,
                          st["max"][1] + off[1] + fov[1] / 2 - 1)
        # head range and grid: the command line, else scans/scan_settings.json (the page's Scan)
        saved = load_json(SETTINGS)
        head = a.head_area or (None if a.area else saved.get("head_area"))
        grid = a.grid or saved.get("grid")
        if head:
            if not (0 <= head[0] < head[2] <= st["max"][0] and 0 <= head[1] < head[3] <= st["max"][1]):
                sys.exit(f"head area {head} must be inside 0..{st['max'][0]:g} x 0..{st['max'][1]:g}")
            # first and last head positions exactly these (picture centres = head + offset)
            area = (head[0] + off[0] - fov[0] / 2, head[1] + off[1] - fov[1] / 2,
                    head[2] + off[0] + fov[0] / 2, head[3] + off[1] + fov[1] / 2)
        if a.save_settings:
            save_json(SETTINGS, {**saved, **({"head_area": list(head)} if head else {}),
                                 **({"grid": list(grid)} if grid else {})})
            print(f"saved in {SETTINGS}: head area {head}, grid {grid}")
        meta["head_area"], meta["grid"] = head and list(head), grid and list(grid)
        meta["max"] = [float(v) for v in st["max"][:2]]  # the travel: --crop-travel
        meta.update(image_size=list(size), fov_mm=[round(fov[0], 2), round(fov[1], 2)],
                    area=list(area), feed=feed, created=datetime.now().isoformat(timespec="seconds"),
                    tiles=[])
        meta["plan"] = []
        for r, c, cx, cy in plan_grid(area, fov, meta["overlap"], grid):
            x, y = cx - off[0], cy - off[1]  # head position for this camera centre
            hx, hy = (min(max(x, 0), st["max"][0]), min(max(y, 0), st["max"][1]))
            if abs(hx - x) > 0.01 or abs(hy - y) > 0.01:
                print(f"  tile r{r} c{c}: head limited to X{hx:.1f} Y{hy:.1f} (outside 0..max)")
                cx, cy = hx + off[0], hy + off[1]
            meta["plan"].append({"row": r, "col": c, "x": round(hx, 2), "y": round(hy, 2),
                                 "cx": round(cx, 2), "cy": round(cy, 2)})
    plan = meta["plan"]
    rows = max(p["row"] for p in plan) + 1
    cols = max(p["col"] for p in plan) + 1
    print(f"grid {cols} x {rows} = {len(plan)} tiles, field {meta['fov_mm'][0]:.1f} x "
          f"{meta['fov_mm'][1]:.1f} mm, overlap {meta['overlap']:.0%}, feed {feed:g} mm/min, "
          f"{meta['mm_per_px']:.4f} mm/px, area {', '.join(f'{v:g}' for v in meta['area'])}")
    if a.dry_run:
        for p in plan:
            print(f"  r{p['row']} c{p['col']}: G0 X{p['x']:.2f} Y{p['y']:.2f} F{feed:g}")
        return

    try:
        b = brightness(cam.image())
    except (OSError, RuntimeError) as e:
        sys.exit(f"camera {a.camera}: {e}")
    if not 8 < b < 250:
        sys.exit(f"the camera picture is {'black' if b <= 8 else 'white'} ({b:.0f}/255): "
                 "light on? lens covered?")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"saving to {folder}")
    done = {t["file"] for t in meta["tiles"] if (folder / t["file"]).exists()}
    before = {}
    view = None
    log = TileLog(folder, meta)
    pending = None  # the tile last taken: (i, p, name, tile record, future of its saving)

    def save_tile(path, grabbed):
        jpg = make_tile(grabbed)
        path.write_bytes(jpg)
        return jpg

    def finish_pending():
        """Wait for the last tile's saving, then show it and log it (in this thread: the
        viewer windows and tiles.log are this thread's)."""
        nonlocal pending
        if pending is None:
            return
        i, p, name, rec, fut = pending
        pending = None
        jpg = fut.result()
        if view:
            view.tile(p, jpg)
        log.add(rec)
        print(f"  {i + 1}/{len(plan)} {name}  X{p['x']:.2f} Y{p['y']:.2f}", flush=True)

    try:
        lock_camera(cam, a, before)
        meta["camera"] = {k: v["value"] for k, v in cam.controls().items()}
        log.save()  # the plan on disk before the first move: --resume works from here on
        if getattr(a, "viewer", None):  # usb_cam.py: the web page's live grid
            view = a.viewer(plan, meta, folder)
        elif a.show:
            view = Viewer(plan, meta, folder)
        # Pipelined: once a tile's frames are in, the head goes on to the next one while a
        # worker averages, encodes and writes them.
        with ThreadPoolExecutor(1) as worker:
            try:
                for i, p in enumerate(plan):
                    name = f"tile_r{p['row']:02d}_c{p['col']:02d}.jpg"
                    if name in done:
                        if view:
                            view.tile(p, (folder / name).read_bytes())
                        continue
                    if view:
                        view.going(p)
                    st = br.move_to(p["x"], p["y"], feed, st, idle=view and view.pump)
                    finish_pending()
                    grabbed = cam.grab_tile(a.frames, a.settle)
                    rec = {**p, "file": name, "pos": st["pos"][:2], "time": time.time()}
                    pending = (i, p, name, rec, worker.submit(save_tile, folder / name, grabbed))
                finish_pending()
            finally:  # a tile already taken is saved even when the scan stops
                try:
                    finish_pending()
                except Exception as e:  # noqa: BLE001 - don't hide why the scan stopped
                    print(f"  the last tile could not be saved: {e}")
                log.save()
    except KeyboardInterrupt:
        print(f"\nstopped: the head stays where it is. Continue with --resume --out {folder}")
        return
    except (RuntimeError, OSError, ValueError, KeyError) as e:  # also: bridge/camera gone
        print(f"scan stopped: {e}. Continue with --resume --out {folder}")
        return
    finally:
        if before and not a.keep_camera:
            cam.set(before)
            print("camera settings put back")
    img = collage(folder, meta, a)
    if view:
        view.finish(img)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    m = ap.add_mutually_exclusive_group()
    m.add_argument("--check", action="store_true",
                   help="move 2 mm in X and Y: camera on the head? mm/px, orientation")
    m.add_argument("--calibrate", action="store_true", help="mm/px from a ruler in the picture")
    m.add_argument("--lens-calibrate", action="store_true", help="checkerboard -> scans/lens.json")
    m.add_argument("--collage-only", metavar="FOLDER", help="rebuild the collage from saved tiles")
    m.add_argument("--offset-calibrate", metavar="X,Y", type=lambda s: tuple(float(v) for v in s.split(",")),
                   help="the tool made a mark at head X,Y; jog until the camera sees it, click it: "
                        "the camera's offset from the tool (collage mm = tool mm)")
    ap.add_argument("--bridge", default="http://m200.local", help="default http://m200.local")
    ap.add_argument("--token", default=os.environ.get("M200_TOKEN"), help="API token (or M200_TOKEN)")
    ap.add_argument("--camera", default="http://127.0.0.1:8081", help="usb_cam.py address")
    ap.add_argument("--mm-per-px", type=float, help="default: scans/calibration.json")
    ap.add_argument("--area", type=lambda s: tuple(float(v) for v in s.split(",")),
                    help="x0,y0,x1,y1 mm, the picture centres' range is this minus half a picture "
                         "(default: the head over its whole travel, 0..max)")
    ap.add_argument("--overlap", type=float, default=0.15,
                    help="least overlap between tiles, 0..0.9, default 0.15 (enough to measure)")
    ap.add_argument("--head-area", type=lambda s: tuple(float(v) for v in s.split(",")),
                    help="x0,y0,x1,y1: first and last head position, e.g. 5,2,135,160 "
                         "(default: scans/scan_settings.json, else the whole travel)")
    ap.add_argument("--save-settings", action="store_true",
                    help="keep --head-area / --grid as the default for every scan (and the page)")
    ap.add_argument("--grid", type=lambda s: tuple(int(v) for v in s.lower().split("x")),
                    help="CxR: this many columns and rows (e.g. 4x4), spread over the area")
    ap.add_argument("--cam-offset", type=lambda s: tuple(float(v) for v in s.split(",")),
                    help="dx,dy mm from the tool (head position) to the picture centre "
                         "(default: calibration.json, from --offset-calibrate, else 0,0)")
    ap.add_argument("--feed", type=float, help="mm/min, default the bridge's jog_f_xy")
    ap.add_argument("--settle", type=float, default=0.5,
                    help="at most this many s to wait after a move for the picture to hold still "
                         "(usually less: it goes on once frames stop moving), default 0.5")
    ap.add_argument("--frames", type=int, default=4,
                    help="frames averaged per tile (less noise; blurred ones left out), default 4")
    ap.add_argument("--rot", type=int, choices=(0, 90, 180, 270), help="clockwise, after --flip-x")
    ap.add_argument("--flip-x", action=argparse.BooleanOptionalAction, default=None,
                    help="mirror left/right (--no-flip-x: not, over calibration.json)")
    ap.add_argument("--flip-y", action=argparse.BooleanOptionalAction, default=None,
                    help="mirror top/bottom (--no-flip-y: not, over calibration.json)")
    ap.add_argument("--exposure", type=int, help="fixed exposure (default: matched to auto)")
    ap.add_argument("--wb", type=int, help="fixed white balance K (default: the slider value)")
    ap.add_argument("--focus", type=int, help="fixed focus (default: sharpest found)")
    ap.add_argument("--keep-camera", action="store_true", help="leave the camera locked at the end")
    ap.add_argument("--undistort", nargs="?", const=str(LENS), help=f"lens file, default {LENS}")
    ap.add_argument("--blend", choices=("multiband", "crop", "feather"), default="multiband",
                    help="multiband (default: each pixel from the best tile, the seams blended "
                         "per frequency: smooth light, sharp detail), crop (the same choice, no "
                         "blending) or feather (soft: ghosting where tiles disagree)")
    ap.add_argument("--no-gain", action="store_true",
                    help="don't even out the tiles' brightness from their overlaps")
    ap.add_argument("--no-whole", action="store_true",
                    help="let seams cut through raised things (pliers, tape) instead of going round")
    ap.add_argument("--meter", choices=("highlights", "mean"), default="highlights",
                    help="exposure (when auto): highlights (default: darker until whites aren't "
                         "blown out, at most 2 steps) or mean (as bright as auto)")
    ap.add_argument("--res", type=float, metavar="MM",
                    help="collage mm per pixel, e.g. 0.1 (default: the camera's own); pixels on "
                         "its multiples, so collages of different scans line up")
    ap.add_argument("--crop-travel", action="store_true",
                    help="collage only over the head travel 0..max (where the tool can go)")
    ap.add_argument("--no-wb", action="store_true",
                    help="keep the colours (no grey-is-grey white balance and brightening)")
    ap.add_argument("--no-glare", action="store_true",
                    help="take each pixel from the nearest tile even where it is blown out or "
                         "shows the light's reflection")
    ap.add_argument("--no-flat", action="store_true",
                    help="keep the bright spot each tile has (no flat-field correction)")
    ap.add_argument("--stitch", action="store_true", help="also try cv2.Stitcher (SCANS mode)")
    ap.add_argument("--no-fit", action="store_true",
                    help="place tiles by the calibration only, don't measure the overlaps")
    ap.add_argument("--dry-run", action="store_true", help="print the grid and moves only")
    ap.add_argument("--show", action="store_true",
                    help="window: each photo in its row/column as it is taken, then the collage")
    ap.add_argument("--resume", action="store_true", help="continue --out (default the last scan)")
    ap.add_argument("--out", help="scan folder, default scans/<date_time>")
    ap.add_argument("--check-mm", type=float, default=2.0, help="--check move, default 2 mm")
    ap.add_argument("--board", default="9x6", help="--lens-calibrate inner corners, default 9x6")
    ap.add_argument("--lens-shots", type=int, default=15)
    return ap


def main():
    a = build_parser().parse_args()
    if not 0 <= a.overlap <= 0.9:
        sys.exit("--overlap must be 0..0.9")

    if a.collage_only:
        folder = Path(a.collage_only)
        meta = load_meta(folder) or sys.exit(f"no tiles.json in {folder}")
        collage(folder, meta, a)
        return
    cam = Cam(a.camera)
    try:
        if a.check:
            mode_check(a, cam)
        elif a.calibrate:
            mode_calibrate(a, cam)
        elif a.offset_calibrate:
            mode_offset(a, cam)
        elif a.lens_calibrate:
            mode_lens(a, cam)
        else:
            mode_scan(a, cam)
    except KeyboardInterrupt:
        print("\nstopped: the head stays where it is")


if __name__ == "__main__":
    main()
