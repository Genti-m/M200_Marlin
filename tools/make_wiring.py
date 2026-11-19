import os, sys
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "wiring_helpers.py"), encoding="utf-8").read())

AXES = [  # axis, addr, ms1, ms2, step, dir
    ("X", 0, "GND", "GND", 4, 5),
    ("Y", 1, "VIO", "GND", 6, 7),
    ("Z", 2, "GND", "VIO", 15, 16),
    ("E", 3, "VIO", "VIO", 17, 18),
]
STOPS = [  # tag, name, gpio, line2
    ("SW", "X min switch", 1, "hit = LOW · homes −X"),
    ("SW", "Y min switch", 2, "hit = LOW · homes −Y"),
    ("SW", "Z max switch", 13, "bottom safety · Z 175"),
    ("PRB", "Z probe (nozzle)", 14, "nozzle touches bed · homes Z"),
]


def fig_wiring():
    slug, W, H = "m200-wiring", 1280, 464
    out = [svg_open(slug, W, H, "ESP32-S3 wiring to four TMC2209 drivers and the M200 endstops",
                    "Four endstop inputs (X min, Y min, Z max, Z probe) wire into an ESP32-S3 DevKit. The ESP32 drives four TMC2209 drivers, X Y Z E at UART addresses 0 to 3, with per-axis STEP and DIR pins, a shared enable, and a shared one-wire UART bus.")]
    out.append('<g transform="translate(0,-80)">')
    mk, lk = f"url(#{slug}-arrow)", f"url(#{slug}-arrow-link)"
    # endstop -> ESP arrows
    for i, (_, _, gpio, _) in enumerate(STOPS):
        cy = 160 + i * 80
        out.append(f'<line x1="240" y1="{cy}" x2="340" y2="{cy}" stroke="{MUTED}" stroke-width="1.2" marker-end="{mk}"/>')
    # buses
    dx0, dw, dgap, dy, dh = 640, 132, 20, 220, 168
    centers = [dx0 + i * (dw + dgap) + dw // 2 for i in range(4)]
    out.append(f'<path d="M560,180 H{centers[-1]}" fill="none" stroke="{MUTED}" stroke-width="1.2"/>')
    out.append(f'<path d="M560,428 H{centers[-1]}" fill="none" stroke="{LINK}" stroke-width="1.2"/>')
    for cx in centers:
        out.append(f'<line x1="{cx}" y1="180" x2="{cx}" y2="{dy}" stroke="{MUTED}" stroke-width="1.2" marker-end="{mk}"/>')
        out.append(f'<line x1="{cx}" y1="428" x2="{cx}" y2="{dy+dh}" stroke="{LINK}" stroke-width="1.2" marker-end="{lk}"/>')
        out.append(f'<circle cx="{cx}" cy="180" r="2.5" fill="{MUTED}"/><circle cx="{cx}" cy="428" r="2.5" fill="{LINK}"/>')
    for i, (_, _, gpio, _) in enumerate(STOPS):
        cy = 160 + i * 80
        out.append(label(252, cy - 20, 76, f"GPIO {gpio}"))
    out.append(label(576, 160, 112, "STEP · DIR · EN"))
    out.append(label(576, 436, 92, "UART1 1-WIRE", LINK))
    # endstop nodes
    for i, (tag, nm, gpio, l2) in enumerate(STOPS):
        y = 126 + i * 80
        out.append(node(40, y, 200, 68, tag, "input", 28))
        out.append(name(140, y + 34, nm))
        out.append(sub(140, y + 48, "pull-up to 3V3 · RC filter"))
        out.append(sub(140, y + 61, l2))
    # ESP32
    out.append(node(340, 120, 220, 320, "MCU", "focal"))
    out.append(name(450, 148, "ESP32-S3 DevKitC-1"))
    out.append(sub(450, 164, "motion firmware · G-code"))
    out.append(esp32_devkit(370, 182))
    out.append(sub(450, 368, "USB-C → host (G-code / ok)"))
    out.append(sub(450, 384, "5 V from 24 V buck"))
    out.append(sub(450, 400, "EN all drivers · GPIO 8"))
    out.append(sub(450, 416, "UART1 · TX 9 · RX 10"))
    # drivers
    for i, (ax, addr, ms1, ms2, st, dr) in enumerate(AXES):
        x = dx0 + i * (dw + dgap)
        cx = x + dw // 2
        out.append(node(x, dy, dw, dh, "DRV", "backend"))
        out.append(name(cx, dy + 34, "TMC2209"))
        out.append(sub(cx, dy + 50, f"{ax} axis · addr {addr}"))
        out.append(sub(cx, dy + 64, f"STEP {st} · DIR {dr}"))
        out.append(nema(cx, dy + 98))
        out.append(sub(cx, dy + 136, "A1 A2 B1 B2 → motor"))
        out.append(sub(cx, dy + 152, "VM 24 V · VIO 3V3"))
    out.append(callout(40, 476, "Z homes when the nozzle touches the bed plate; Z max only stops a crash at the bottom."))
    out.append(callout(640, 476, "Each driver: 100 µF on VM, ground shared with the ESP32."))
    out.append(legend(496, W, [
        ("box", "ESP32-S3 (focal)", (ACC_TINT, ACCENT, False)),
        ("box", "Driver + motor", (PAPER2, INK, False)),
        ("box", "Endstop / probe input", ("rgba(191,192,192,0.10)", SOFT, False)),
        ("line", "Digital signal", (MUTED, f"{slug}-arrow", False)),
        ("line", "UART config bus", (LINK, f"{slug}-arrow-link", False)),
    ]))
    out.append('</g></svg>')
    return "".join(out)


def fig_uart():
    slug, W, H = "tmc-uart", 1280, 316
    out = [svg_open(slug, W, H, "TMC2209 one-wire UART bus and address straps",
                    "ESP32-S3 UART1 TX on GPIO 9 goes through a 1 kilo-ohm resistor to the shared PDN_UART line; RX on GPIO 10 connects to the line directly. Four TMC2209 drivers sit on the line, addressed 0 to 3 by their MS1 and MS2 straps.")]
    out.append('<g transform="translate(0,-36)">')
    lk, mk = f"url(#{slug}-arrow-link)", f"url(#{slug}-arrow)"
    dx0, dw, dgap, dy, dh = 520, 164, 20, 168, 100
    centers = [dx0 + i * (dw + dgap) + dw // 2 for i in range(4)]
    # TX: ESP -> resistor -> junction
    out.append(f'<line x1="260" y1="104" x2="304" y2="104" stroke="{LINK}" stroke-width="1.2"/>')
    out.append(f'<line x1="344" y1="104" x2="420" y2="104" stroke="{LINK}" stroke-width="1.2"/>')
    # RX: junction -> ESP (dashed, reply)
    out.append(f'<path d="M420,104 V152 H260" fill="none" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5,4" marker-end="{mk}"/>')
    # bus
    out.append(f'<path d="M420,104 H{centers[-1]}" fill="none" stroke="{LINK}" stroke-width="1.2"/>')
    for cx in centers:
        out.append(f'<line x1="{cx}" y1="104" x2="{cx}" y2="{dy}" stroke="{LINK}" stroke-width="1.2" marker-end="{lk}"/>')
        out.append(f'<circle cx="{cx}" cy="104" r="2.5" fill="{LINK}"/>')
    out.append(f'<circle cx="420" cy="104" r="3" fill="{LINK}"/>')
    # resistor (focal part)
    out.append(f'<rect x="304" y="98" width="40" height="12" rx="1" fill="{ACC_TINT}" stroke="{ACCENT}" stroke-width="1.2"/>')
    out.append(label(268, 80, 32, "TX 9", LINK))
    out.append(label(300, 116, 48, "1 KΩ", ACCENT))
    out.append(label(280, 160, 48, "RX 10"))
    out.append(label(440, 84, 96, "PDN_UART BUS", LINK))
    # ESP node
    out.append(node(40, 64, 220, 120, "MCU", "backend"))
    out.append(esp32_chip(56, 100))
    out.append(name(170, 116, "ESP32-S3", INK))
    out.append(sub(170, 132, "UART1 · 115200 8N1"))
    out.append(sub(170, 146, "half duplex, echo"))
    out.append(sub(170, 160, "read back"))
    for i, (ax, addr, ms1, ms2, _, _) in enumerate(AXES):
        x = dx0 + i * (dw + dgap)
        cx = x + dw // 2
        out.append(node(x, dy, dw, dh, "DRV", "backend"))
        out.append(name(cx, dy + 34, f"TMC2209 · {ax}"))
        out.append(sub(cx, dy + 52, f"address {addr}"))
        out.append(sub(cx, dy + 68, f"MS1 {ms1} · MS2 {ms2}"))
        out.append(sub(cx, dy + 84, "PDN_UART on bus"))
    out.append(callout(40, 312, "MS1/MS2 only set the UART address now; microsteps are written over the wire."))
    out.append(callout(720, 312, "The resistor stops TX from fighting a driver's reply."))
    out.append('</g></svg>')
    return "".join(out)


EXTRA_CSS = f"""
table {{ width:100%; border-collapse:collapse; font-size:0.86rem; margin:0.5rem 0 1.5rem; }}
th {{ font-family:{MONO}; font-size:0.62rem; letter-spacing:0.14em; text-transform:uppercase; color:var(--soft); font-weight:500; text-align:left; padding:8px 10px; border-bottom:1px solid var(--rule); }}
td {{ padding:8px 10px; border-bottom:1px solid var(--rule); color:var(--muted); vertical-align:top; }}
td:first-child {{ color:var(--ink); font-weight:600; }}
td code, li code, pre {{ font-family:{MONO}; color:var(--ink); font-size:0.8rem; }}
.table-wrap {{ overflow-x:auto; }}
pre {{ background:rgba(245,245,245,0.03); border:1px solid var(--rule); border-radius:6px; padding:14px 16px; line-height:1.6; overflow-x:auto; }}
pre .c {{ color:var(--soft); }}
.two {{ display:grid; grid-template-columns:1fr 1.15fr; gap:14px; }}
@media (max-width: 860px) {{ .two {{ grid-template-columns:1fr; }} }}
"""

PIN_ROWS = [
    ("X STEP / DIR", "GPIO 4 / 5", "TMC2209 #0 STEP, DIR", ""),
    ("Y STEP / DIR", "GPIO 6 / 7", "TMC2209 #1 STEP, DIR", ""),
    ("Z STEP / DIR", "GPIO 15 / 16", "TMC2209 #2 STEP, DIR", ""),
    ("E STEP / DIR", "GPIO 17 / 18", "TMC2209 #3 STEP, DIR", ""),
    ("EN (all)", "GPIO 8", "EN on all four drivers", "Active low. 10 kΩ pull-up to 3V3 so motors stay off during boot"),
    ("UART TX", "GPIO 9", "1 kΩ → PDN_UART bus", "UART1"),
    ("UART RX", "GPIO 10", "PDN_UART bus, direct", "Reads its own echo plus replies"),
    ("DIAG X / Y", "GPIO 11 / 12", "DIAG of drivers #0, #1", "Optional: only for sensorless homing"),
    ("X min", "GPIO 1", "X endstop (stock PE12)", "Switch to GND, hit = LOW"),
    ("Y min", "GPIO 2", "Y endstop (stock PC6)", "Switch to GND, hit = LOW"),
    ("Z max", "GPIO 13", "Z bottom switch (stock PA8)", "Safety stop, hit = LOW"),
    ("Z probe", "GPIO 14", "Nozzle-to-bed contact (stock PC7)", "Used for Z homing and bed probing"),
    ("E-stop in", "GPIO 21", "Panel button to GND", "Optional: hardware stop, cuts EN in firmware"),
]

AXIS_ROWS = [
    ("X", "0 · GND / GND", "−X to min switch", "640", "StealthChop", "0.8 A"),
    ("Y", "1 · VIO / GND", "−Y to min switch", "640", "StealthChop", "0.8 A"),
    ("Z", "2 · GND / VIO", "−Z on nozzle probe, Z max as guard", "800", "StealthChop", "0.8 A"),
    ("E", "3 · VIO / VIO", "none (optional runout sensor)", "960", "SpreadCycle", "0.65 A"),
]


def tables():
    pins = "".join(f"<tr><td>{a}</td><td><code>{b}</code></td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in PIN_ROWS)
    axes = "".join(f"<tr><td>{a}</td><td><code>{b}</code></td><td>{c}</td><td><code>{d}</code></td><td>{e}</td><td><code>{f}</code></td></tr>" for a, b, c, d, e, f in AXIS_ROWS)
    return f"""
  <section>
    <h2><span>03</span>Pin map</h2>
    <p class="note">Pins chosen for an ESP32-S3-DevKitC-1 (N16R8). They avoid the strapping pins 0, 3, 45 and 46, the USB pins 19 and 20, and GPIO 26 to 37, which the flash and octal PSRAM use.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Signal</th><th>ESP32-S3</th><th>Goes to</th><th>Notes</th></tr></thead>
      <tbody>{pins}</tbody></table></div>
  </section>

  <section>
    <h2><span>04</span>Per-axis configuration</h2>
    <p class="note">Steps/mm are the stock Marlin values for the V4. They hold only if the TMC2209 runs the same microstep setting the stock A4988s used, so check that first. Change the microsteps and steps/mm scale with them. The currents are safe starting points; raise them toward about 70% of the rating on each motor's label.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Axis</th><th>Addr · MS1 / MS2</th><th>Homing / endstop</th><th>Steps/mm</th><th>Chopper</th><th>Start current (RMS)</th></tr></thead>
      <tbody>{axes}</tbody></table></div>
    <div class="two">
      <pre><span class="c">; init block the module runs at boot (Marlin syntax)</span>
M350 X16 Y16 Z16 E16      <span class="c">; microsteps = stock</span>
M92  X640 Y640 Z800 E960  <span class="c">; stock steps/mm</span>
M906 X800 Y800 Z800 E650  <span class="c">; run current, mA</span>
M569 S1 X Y Z             <span class="c">; StealthChop</span>
M569 S0 E                 <span class="c">; SpreadCycle for extrusion</span>
M211 S1                   <span class="c">; soft limits 200 × 200 × 175</span>
<span class="c">; homing order: X, Y, then Z on the probe at bed centre</span></pre>
      <div class="card focal"><h3>Check before first power-up</h3><ul>
        <li>Measure every endstop wire with the stock board unplugged. ESP32-S3 pins take 3.3 V only, so no 5 V pull-up may stay on the harness.</li>
        <li>The Z probe is a circuit through the nozzle and bed plate. Check it isn't tied to the heater's 24 V; if unsure, use an optocoupler.</li>
        <li>Write the driver currents over UART before EN goes low.</li>
        <li>Never plug or unplug a motor while VM is live. It kills TMC2209s.</li>
        <li>Out of scope here: the hotend thermocouple (AD849x), bed NTC, heaters and fans. Replacing the board means a thermal module with its own MINTEMP and runaway checks.</li>
      </ul></div>
    </div>
  </section>
"""


def page():
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>M200 TMC2209 wiring</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&family=Noto+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>{CSS}{EXTRA_CSS}</style>
</head>
<body>
<div class="frame">
  <p class="eyebrow">Architecture · Wiring · Diagram Design</p>
  <h1>Replacing the M200 V4 board with one ESP32-S3 motion module</h1>
  <p class="lede">How the four TMC2209 drivers, the motors and the M200's own endstops wire to an ESP32-S3 once the Zortrax board is removed, and how each axis is set up.</p>

  <section>
    <h2><span>01</span>Wiring overview</h2>
    <p class="note">Every driver gets its own STEP and DIR pin. Enable and the UART wire are shared. The four stock endstop inputs move from the STM32 pins to ESP32 GPIOs.</p>
    <div class="diagram-container">{fig_wiring()}</div>
  </section>

  <section>
    <h2><span>02</span>The one-wire UART bus</h2>
    <p class="note">All four drivers share a single PDN_UART line. Each one answers only to its own address, which is set by tying MS1 and MS2 to GND or VIO.</p>
    <div class="diagram-container">{fig_uart()}</div>
  </section>
{tables()}
</div>
</body>
</html>
"""


with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(page())
print("wrote", sys.argv[1])
