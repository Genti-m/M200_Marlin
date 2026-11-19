import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
exec(open(os.path.join(HERE, "wiring_helpers.py"), encoding="utf-8").read())
_src = open(os.path.join(HERE, "make_axis_wiring.py"), encoding="utf-8").read()
exec("# " + _src.split("# ---------- schematic glyphs")[1].split("# ---------- figure 1")[0])

AX = [("X", 0, "GND", "GND", 4, 5, "R11", "R12"), ("Y", 1, "VIO", "GND", 6, 7, "R13", "R14"),
      ("Z", 2, "GND", "VIO", 15, 16, "R15", "R16"), ("E", 3, "VIO", "VIO", 17, 18, "R17", "R18")]


# ---------------- figure 1: one supply, four drivers ----------------
def fig_power():
    slug, W, H = "four-axis-power", 1280, 700
    out = [svg_open(slug, W, H, "Four axes on one 24 V supply",
                    "One 24 V supply passes fuse F1, ideal diode Q1, TVS D1, bulk capacitor C1 and relay contact K1 onto a VM bus feeding four TMC2209 carriers and motors; a second branch through polyfuse F2, buck U2 and Schottky D3 powers the ESP32-S3, which drives relay K1 through MOSFET Q2 in series with E-stop S1.")]
    out.append('<g transform="translate(0,-20)">')
    P, G = 120, 340
    centers = [630, 794, 958, 1122]
    out.append(wire(160, P, 460, P, ACCENT, 1.6))
    out.append(wire(484, P, 1122, P, ACCENT, 1.6))
    out.append(wire(160, G, 1122, G, MUTED, 1.6))
    for x in (330, 390):
        out.append(wire(x, P, x, G))
    for cx in centers:
        out.append(wire(cx, P, cx, 180, ACCENT, 1.6))
        out.append(wire(cx, 300, cx, G, MUTED, 1.4))
    # logic tap with a hop over the GND rail
    out.append(f'<path d="M430,{P} V334 A6,6 0 0 1 430,346 V432 Q430,440 438,440 H520" fill="none" stroke="{MUTED}" stroke-width="1.2"/>')
    out.append(f'<path d="M496,440 V568 Q496,576 504,576 H960" fill="none" stroke="{MUTED}" stroke-width="1.2"/>')
    out.append(wire(640, 440, 740, 440))
    out.append(wire(900, 440, 960, 440))
    out.append(wire(1040, 548, 1040, 488))
    out.append(label(684, 100, 56, "VM BUS", ACCENT))
    out.append(label(438, 384, 36, "24 V"))
    out.append(label(644, 420, 32, "5 V"))
    out.append(label(902, 420, 56, "GPIO 38"))
    out.append(label(700, 556, 104, "24 V AFTER F2"))
    out.append(label(1048, 512, 56, "NC LOOP"))
    # PSU
    out.append(node(40, 88, 120, 284, "PSU", "backend", 32))
    out.append(name(100, 220, "24 V PSU"))
    out.append(sub(100, 236, "one supply"))
    out.append(pin(148, P, "V+", "end"))
    out.append(pin(148, G, "V−", "end"))
    # series + shunt parts
    out.append(fuse(200, P) + comp(200, 100, "F1 · 5 A T"))
    out.append(diode_h(272, P) + comp(272, 88, "Q1 · ideal diode") + comp(272, 100, "LM74700 + MOSFET"))
    out.append(contact(460, 484, P) + comp(472, 76, "K1 contact") + comp(472, 88, "coil driver below"))
    out.append(tvs_v(330, 230) + cap_v(390, 230, True))
    for x in (330, 390):
        out.append(dot(x, P, ACCENT) + dot(x, G, MUTED))
    out.append(dot(430, P, ACCENT))
    out.append(comp(330, 360, "D1 SMBJ26A"))
    out.append(comp(390, 372, "C1 1000 µF") + comp(390, 384, "35 V"))
    out.append(dot(260, G, ACCENT, 5) + comp(260, 360, "star GND", ACCENT))
    for cx in centers:
        out.append(dot(cx, P, ACCENT) + dot(cx, G, MUTED))
    # carriers
    for i, (ax, addr, *_rest) in enumerate(AX):
        x = 560 + i * 164
        cx = x + 70
        out.append(node(x, 180, 140, 120, "DRV", "backend"))
        out.append(name(cx, 208, f"{ax} · TMC2209"))
        out.append(sub(cx, 224, f"C{11+i} 100 µF · C{15+i} 100 nF"))
        out.append(nema(cx, 254, 26))
        out.append(sub(cx, 288, f"6-wire motor {ax}"))
    # logic branch parts
    out.append(fuse(460, 440) + comp(460, 428, "F2 1 A PTC"))
    out.append(dot(496, 440))
    out.append(diode_h(690, 440) + comp(690, 462, "D3 SS34"))
    out.append(node(520, 404, 120, 72, "DC", "backend"))
    out.append(name(580, 434, "U2 buck"))
    out.append(sub(580, 450, "24 → 5 V · 2 A"))
    out.append(sub(580, 464, "D24V22F5"))
    out.append(node(740, 392, 160, 96, "MCU", "backend"))
    out.append(name(820, 428, "ESP32-S3"))
    out.append(sub(820, 446, "5V pin · GND to star"))
    out.append(sub(820, 460, "stays on in E-stop"))
    out.append(node(960, 392, 160, 96, "K1", "focal", 24))
    out.append(name(1040, 420, "K1 coil driver"))
    out.append(sub(1040, 436, "Q2 AO3400 low side"))
    out.append(sub(1040, 450, "R1 100 Ω · R2 100 kΩ"))
    out.append(sub(1040, 464, "D2 1N4148 flyback"))
    out.append(node(960, 548, 160, 56, "S1", "input", 24))
    out.append(name(1040, 572, "E-stop S1"))
    out.append(sub(1040, 588, "NC · 2nd NC → GPIO 21"))
    out.append(callout(40, 648, "K1 drops when S1 opens or GPIO 38 goes low; the ESP32 stays powered to report it."))
    out.append(legend(664, W, [
        ("box", "K1 relay driver (focal)", (ACC_TINT, ACCENT, False)),
        ("box", "Driver / module", (PAPER2, INK, False)),
        ("line", "+24 V motor rail", (ACCENT, None, False)),
        ("line", "Ground / logic supply", (MUTED, None, False)),
    ]))
    out.append('</g></svg>')
    return "".join(out)


# ---------------- figure 2: logic to the four drivers ----------------
def fig_logic():
    slug, W, H = "four-axis-logic", 1280, 720
    out = [svg_open(slug, W, H, "ESP32-S3 logic wiring to four TMC2209s",
                    "Each driver gets its own STEP and DIR from the ESP32-S3 through a 220 ohm resistor; EN with a 10k pull-up, UART through 1k with RX direct, 3V3 and ground leave the ESP32 as one shared four-wire bus that reaches every driver.")]
    out.append('<g transform="translate(0,-16)">')
    for i, (ax, addr, ms1, ms2, st, dr, rs, rd) in enumerate(AX):
        y0 = 40 + 116 * i
        for y in (y0 + 36, y0 + 64):
            out.append(wire(240, y, 840, y))
    out.append(wire(240, 504, 680, 504))
    out.append(wire(560, 504, 560, 476))
    out.append(wire(240, 528, 680, 528, LINK))
    out.append(f'<path d="M240,552 H592 Q600,552 600,544 V528" fill="none" stroke="{LINK}" stroke-width="1.2"/>')
    out.append(wire(240, 576, 680, 576))
    out.append(wire(240, 600, 680, 600))
    # bus
    out.append(f'<line x1="680" y1="500" x2="680" y2="604" stroke="{SOFT}" stroke-width="4"/>')
    out.append(f'<path d="M680,552 H1112 Q1120,552 1120,544 V90" fill="none" stroke="{SOFT}" stroke-width="4"/>')
    for i in range(4):
        out.append(f'<line x1="1060" y1="{90+116*i}" x2="1120" y2="{90+116*i}" stroke="{SOFT}" stroke-width="4"/>')
    out.append(label(840, 560, 100, "SHARED BUS ×4"))
    # parts
    for i, (ax, addr, ms1, ms2, st, dr, rs, rd) in enumerate(AX):
        y0 = 40 + 116 * i
        out.append(res_h(520, y0 + 36) + comp(536, y0 + 26, f"{rs} 220 Ω"))
        out.append(res_h(520, y0 + 64) + comp(536, y0 + 54, f"{rd} 220 Ω"))
    out.append(res_h(400, 504) + comp(416, 494, "R20 220 Ω"))
    out.append(res_v(560, 480) + bar(560, 476) + dot(560, 504))
    out.append(comp(572, 490, "R21 10 kΩ → 3V3", SOFT, "start"))
    out.append(res_h(400, 528) + comp(416, 520, "R22 1 kΩ", LINK))
    out.append(dot(600, 528, LINK))
    out.append(comp(256, 546, "RX direct", MUTED, "start"))
    for y in (504, 528, 576, 600):
        out.append(dot(680, y, SOFT))
    # ESP
    out.append(node(40, 40, 200, 584, "MCU", "focal"))
    out.append(esp32_chip(52, 64))
    rows = []
    for i, (ax, addr, ms1, ms2, st, dr, rs, rd) in enumerate(AX):
        y0 = 40 + 116 * i
        rows += [(y0 + 36, f"GPIO {st} · {ax} STEP"), (y0 + 64, f"GPIO {dr} · {ax} DIR")]
    rows += [(504, "GPIO 8 · EN"), (528, "GPIO 9 · TX1"), (552, "GPIO 10 · RX1"), (576, "3V3"), (600, "GND")]
    for y, t in rows:
        out.append(pin(228, y, t, "end"))
    out.append(comp(72, 108, "ESP32-S3", INK))
    # drivers
    for i, (ax, addr, ms1, ms2, st, dr, rs, rd) in enumerate(AX):
        y0 = 40 + 116 * i
        out.append(node(840, y0, 220, 100, "DRV", "backend"))
        out.append(name(970, y0 + 24, f"{ax} · TMC2209"))
        out.append(sub(970, y0 + 42, f"address {addr}"))
        out.append(sub(970, y0 + 56, f"MS1 {ms1} · MS2 {ms2}"))
        out.append(sub(970, y0 + 70, f"C{21+i} 100 nF on VIO"))
        out.append(pin(852, y0 + 36, "STEP", "start"))
        out.append(pin(852, y0 + 64, "DIR", "start"))
        out.append(sub(1048, y0 + 86, "EN UART VIO GND", SOFT, "end"))
    out.append(callout(40, 664, "Each driver gets its own STEP and DIR; EN, UART, VIO and GND are one bus to all four."))
    out.append(legend(680, W, [
        ("box", "ESP32-S3 (focal)", (ACC_TINT, ACCENT, False)),
        ("line", "Own signal", (MUTED, None, False)),
        ("line", "UART", (LINK, None, False)),
        ("line", "Shared 4-wire bus", (SOFT, None, False)),
    ]))
    out.append('</g></svg>')
    return "".join(out)


# ---------------- figure 3: inputs ----------------
IN = [("GPIO 1", "X min switch", "COM → wire · NO → GND · hit LOW", "backend"),
      ("GPIO 2", "Y min switch", "COM → wire · NO → GND · hit LOW", "backend"),
      ("GPIO 13", "Z max switch", "bottom safety · hit LOW", "backend"),
      ("GPIO 14", "Z probe", "nozzle ↔ bed · measure volts first", "focal"),
      ("GPIO 21", "E-stop S1, 2nd NC", "pressed → opens → reads HIGH", "input")]


def fig_inputs():
    slug, W, H = "four-axis-inputs", 1280, 520
    out = [svg_open(slug, W, H, "Endstop and E-stop inputs",
                    "Each of the five inputs (X min, Y min, Z max, Z probe, E-stop) reaches its ESP32-S3 GPIO through a 10k pull-up to 3.3 V, a 1k series resistor and a 100 nF capacitor to ground at the pin.")]
    for i, (gp, nm, s, kind) in enumerate(IN):
        y = 72 + 80 * i
        out.append(wire(240, y, 760, y))
        out.append(wire(320, y, 320, y + 32))
        out.append(wire(600, y, 600, y - 28))
        out.append(cap_v(320, y + 16) + ground(320, y + 32) + dot(320, y))
        out.append(comp(336, y + 28, f"C{31+i} 100 nF", SOFT, "start"))
        out.append(res_h(448, y) + comp(464, y - 10, f"R{36+i} 1 kΩ"))
        out.append(res_v(600, y - 24) + bar(600, y - 28) + dot(600, y))
        out.append(comp(612, y - 12, f"R{31+i} 10 kΩ → 3V3", SOFT, "start"))
        out.append(node(760, y - 28, 260, 56, "IN", kind, 24))
        out.append(name(890, y - 4, nm))
        out.append(sub(890, y + 12, s))
    out.append(node(40, 40, 200, 392, "MCU", "backend"))
    out.append(name(140, 404, "ESP32-S3"))
    for i, (gp, *_r) in enumerate(IN):
        out.append(pin(228, 72 + 80 * i, gp, "end"))
    out.append(callout(40, 468, "Same three parts on every input: pull-up, series resistor, filter capacitor."))
    out.append(legend(480, W, [
        ("box", "Z probe: check first (focal)", (ACC_TINT, ACCENT, False)),
        ("box", "Switch input", (PAPER2, INK, False)),
        ("line", "3.3 V signal", (MUTED, None, False)),
    ]))
    out.append('</svg>')
    return "".join(out)


BOM = [
    ("Power", None),
    ("F1", "Fuse, slow-blow, in holder", "5 A T", 1, "24 V motor branch"),
    ("F2", "PTC polyfuse", "1 A hold", 1, "24 V logic branch"),
    ("Q1", "Ideal-diode controller + N-MOSFET (or ready anti-reverse module)", "LM74700-Q1, 40 V MOSFET", 1, "reverse polarity"),
    ("D1", "TVS diode, unidirectional", "SMBJ26A", 1, "cathode to +24 V"),
    ("C1", "Electrolytic, low-ESR", "1000 µF 35 V", 1, "rail entry"),
    ("K1", "Relay, 24 V coil, NO contact", "≥ 10 A contact", 1, "switches the VM bus"),
    ("Q2", "Logic-level N-MOSFET", "AO3400", 1, "drives the K1 coil"),
    ("R1", "Resistor", "100 Ω", 1, "Q2 gate series"),
    ("R2", "Resistor", "100 kΩ", 1, "Q2 gate to GND, keeps K1 off at boot"),
    ("D2", "Diode", "1N4148 or 1N4007", 1, "across K1 coil, cathode to +"),
    ("S1", "E-stop mushroom, two NC contact blocks", "latching", 1, "NC 1 in coil loop, NC 2 to GPIO 21"),
    ("U2", "Buck 24 → 5 V", "Pololu D24V22F5", 1, "ESP32 supply"),
    ("D3", "Schottky diode", "SS34", 1, "U2 → ESP32 5V pin, so USB can't back-feed"),
    ("Drivers", None),
    ("—", "TMC2209 stepstick + carrier board", "BTT TMC2209", 4, "MS3 off, cut RST–SLP bridge"),
    ("C11–C14", "Electrolytic (usually on carrier)", "100 µF 35 V", 4, "VM to GND per driver"),
    ("C15–C18", "Ceramic", "100 nF", 4, "VM to GND per driver"),
    ("C21–C24", "Ceramic", "100 nF", 4, "VIO to GND per driver"),
    ("Logic", None),
    ("R11–R18", "Resistor", "220 Ω", 8, "STEP and DIR, one per line"),
    ("R20", "Resistor", "220 Ω", 1, "shared EN"),
    ("R21", "Resistor", "10 kΩ", 1, "EN pull-up to 3V3"),
    ("R22", "Resistor", "1 kΩ", 1, "UART TX"),
    ("Inputs", None),
    ("R31–R35", "Resistor", "10 kΩ", 5, "pull-up to 3V3"),
    ("R36–R40", "Resistor", "1 kΩ", 5, "series into the GPIO"),
    ("C31–C35", "Ceramic", "100 nF", 5, "GPIO to GND"),
]

PINS = [
    ("GPIO 4 / 5", "X STEP / DIR", "R11 / R12 220 Ω", "X driver"),
    ("GPIO 6 / 7", "Y STEP / DIR", "R13 / R14 220 Ω", "Y driver"),
    ("GPIO 15 / 16", "Z STEP / DIR", "R15 / R16 220 Ω", "Z driver"),
    ("GPIO 17 / 18", "E STEP / DIR", "R17 / R18 220 Ω", "E driver"),
    ("GPIO 8", "EN, all four", "R20 220 Ω, R21 10 kΩ to 3V3", "EN of every driver"),
    ("GPIO 9", "UART TX", "R22 1 kΩ", "PDN_UART of every driver"),
    ("GPIO 10", "UART RX", "direct", "PDN_UART bus, after R22"),
    ("GPIO 1", "X min", "R31, R36, C31", "X switch"),
    ("GPIO 2", "Y min", "R32, R37, C32", "Y switch"),
    ("GPIO 13", "Z max", "R33, R38, C33", "Z bottom switch"),
    ("GPIO 14", "Z probe", "R34, R39, C34", "nozzle / bed contact"),
    ("GPIO 21", "E-stop sense", "R35, R40, C35", "S1 second NC block"),
    ("GPIO 38", "K1 enable", "R1 100 Ω, R2 100 kΩ to GND", "Q2 gate"),
    ("GPIO 11 / 12", "DIAG X / Y, optional", "direct", "sensorless homing only"),
    ("3V3", "Logic supply", "C21–C24 at each driver", "VIO ×4, all pull-ups"),
    ("5V", "Board supply", "D3 from U2", "buck output"),
    ("GND", "Ground", "star point", "PSU V−, every driver GND, U2"),
    ("Free", "39, 40, 41, 42, 47, 48", "+ 43 / 44 without the UART0 port", ""),
]


def tables():
    b = []
    for r in BOM:
        if r[1] is None:
            b.append(f'<tr class="group"><td colspan="5">{r[0]}</td></tr>')
        else:
            b.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    p = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in PINS)
    return "".join(b), p


EXTRA_CSS = f"""
table {{ width:100%; border-collapse:collapse; font-size:0.86rem; margin:0.5rem 0 1.5rem; }}
th {{ font-family:{MONO}; font-size:0.62rem; letter-spacing:0.14em; text-transform:uppercase; color:var(--soft); font-weight:500; text-align:left; padding:8px 10px; border-bottom:1px solid var(--rule); }}
td {{ padding:7px 10px; border-bottom:1px solid var(--rule); color:var(--muted); vertical-align:top; }}
td:first-child {{ color:var(--ink); font-weight:600; white-space:nowrap; font-family:{MONO}; font-size:0.8rem; }}
tr.group td {{ color:var(--soft); font-family:{MONO}; font-size:0.62rem; letter-spacing:0.16em; text-transform:uppercase; font-weight:500; padding-top:16px; }}
.table-wrap {{ overflow-x:auto; }}
"""


def page():
    bom, pins = tables()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Four-axis motor wiring</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&family=Noto+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>{CSS}{EXTRA_CSS}</style>
</head>
<body>
<div class="frame">
  <p class="eyebrow">Architecture · Schematic · Diagram Design</p>
  <h1>Four steppers, one supply, one ESP32-S3</h1>
  <p class="lede">The full M200 motion wiring: one 24 V supply and its protections feeding four TMC2209 drivers and motors, every logic wire from the ESP32-S3 with its resistor, all five inputs with their filters, and the parts list.</p>

  <section>
    <h2><span>01</span>Power: one supply, four drivers</h2>
    <p class="note">The motor rail and the ESP32's supply split right after the reverse-polarity diode. K1 cuts only the motor rail, so the ESP32 keeps running and can report the E-stop. Each motor's six wires connect exactly as on the one-axis page: four to A1 A2 B1 B2, both centre taps unused.</p>
    <div class="diagram-container">{fig_power()}</div>
  </section>

  <section>
    <h2><span>02</span>Logic: ESP32-S3 to the four drivers</h2>
    <p class="note">Eight own lines (STEP and DIR per axis) and one shared four-wire bus. The address straps let all four drivers share the UART wire.</p>
    <div class="diagram-container">{fig_logic()}</div>
  </section>

  <section>
    <h2><span>03</span>Inputs: endstops, probe and E-stop</h2>
    <div class="diagram-container">{fig_inputs()}</div>
  </section>

  <section>
    <h2><span>04</span>Parts list</h2>
    <p class="note">23 resistors, 18 capacitors (plus the 8 already on the carriers), 3 diodes, 2 fuses, 1 TVS, 2 MOSFETs, 1 relay, 1 buck module.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Ref</th><th>Part</th><th>Value</th><th>Qty</th><th>Where</th></tr></thead>
      <tbody>{bom}</tbody></table></div>
  </section>

  <section>
    <h2><span>05</span>Every ESP32-S3 connection</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Pin</th><th>Signal</th><th>Through</th><th>To</th></tr></thead>
      <tbody>{pins}</tbody></table></div>
  </section>
</div>
</body>
</html>
"""


with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(page())
print("wrote", sys.argv[1])
