import os, sys
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "wiring_helpers.py"), encoding="utf-8").read())


# ---------- schematic glyphs (decorative, drawn over the wire) ----------
def res_h(x, y):
    return f'<rect x="{x}" y="{y-6}" width="32" height="12" rx="1" fill="{PAPER}" stroke="{INK}" stroke-width="1.2"/>'


def res_v(x, y0):
    return f'<rect x="{x-6}" y="{y0}" width="12" height="20" rx="1" fill="{PAPER}" stroke="{INK}" stroke-width="1.2"/>'


def fuse(x, y):
    return (f'<rect x="{x-12}" y="{y-6}" width="24" height="12" rx="1" fill="{PAPER}" stroke="{INK}" stroke-width="1.2"/>'
            f'<line x1="{x-12}" y1="{y}" x2="{x+12}" y2="{y}" stroke="{INK}" stroke-width="0.8"/>')


def diode_h(x, y):
    return (f'<rect x="{x-10}" y="{y-9}" width="20" height="18" fill="{PAPER}"/>'
            f'<polygon points="{x-8},{y-8} {x+8},{y} {x-8},{y+8}" fill="{INK}"/>'
            f'<line x1="{x+8}" y1="{y-8}" x2="{x+8}" y2="{y+8}" stroke="{INK}" stroke-width="1.4"/>')


def tvs_v(x, y):
    return (f'<rect x="{x-10}" y="{y-10}" width="20" height="20" fill="{PAPER}"/>'
            f'<polygon points="{x-8},{y+8} {x+8},{y+8} {x},{y-8}" fill="{INK}"/>'
            f'<path d="M{x-10},{y-6} V{y-8} H{x+10} V{y-10}" fill="none" stroke="{INK}" stroke-width="1.4"/>')


def cap_v(x, y, polar=False):
    o = (f'<rect x="{x-11}" y="{y-4}" width="22" height="8" fill="{PAPER}"/>'
         f'<line x1="{x-10}" y1="{y-4}" x2="{x+10}" y2="{y-4}" stroke="{INK}" stroke-width="1.6"/>'
         f'<line x1="{x-10}" y1="{y+4}" x2="{x+10}" y2="{y+4}" stroke="{INK}" stroke-width="1.6"/>')
    if polar:
        o += f'<text x="{x-16}" y="{y-6}" fill="{SOFT}" font-size="9" font-family="{MONO}">+</text>'
    return o


def ground(x, y):
    return (f'<line x1="{x-8}" y1="{y}" x2="{x+8}" y2="{y}" stroke="{INK}" stroke-width="1.2"/>'
            f'<line x1="{x-5}" y1="{y+3}" x2="{x+5}" y2="{y+3}" stroke="{INK}" stroke-width="1.2"/>'
            f'<line x1="{x-2}" y1="{y+6}" x2="{x+2}" y2="{y+6}" stroke="{INK}" stroke-width="1.2"/>')


def bar(x, y):
    return f'<line x1="{x-10}" y1="{y}" x2="{x+10}" y2="{y}" stroke="{INK}" stroke-width="1.4"/>'


def contact(x1, x2, y):
    return (f'<rect x="{x1-4}" y="{y-14}" width="{x2-x1+8}" height="20" fill="{PAPER}"/>'
            f'<circle cx="{x1}" cy="{y}" r="2.5" fill="{INK}"/><circle cx="{x2}" cy="{y}" r="2.5" fill="{INK}"/>'
            f'<path d="M{x1},{y} L{x2-2},{y-11}" fill="none" stroke="{INK}" stroke-width="1.4"/>')


def dot(x, y, c=INK, r=3):
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c}"/>'


def comp(cx, y, text, color=SOFT, anchor="middle"):
    return f'<text x="{cx}" y="{y}" fill="{color}" font-size="8.5" font-family="{MONO}" text-anchor="{anchor}">{text}</text>'


def pin(x, y, text, anchor):
    return f'<text x="{x}" y="{y+3}" fill="{INK}" font-size="9" font-family="{MONO}" text-anchor="{anchor}">{text}</text>'


def wire(x1, y1, x2, y2, color=MUTED, w=1.2):
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{w}"/>'


# ---------- figure 1: 24 V path + the motor's six wires ----------
def fig_power():
    slug, W, H = "axis-power", 1280, 468
    out = [svg_open(slug, W, H, "One axis: 24 V path and the motor's six wires",
                    "24 V runs from the PSU through fuse F1, ideal diode Q1, a TVS and 1000 microfarad bulk capacitor, then relay contact K1 and the carrier's local capacitors to the TMC2209 VM pin; ground returns through a star point; the motor's four coil ends go to A1, A2, B1, B2 and its two centre taps stay unconnected.")]
    out.append('<g transform="translate(0,-24)">')
    P, G = 120, 300
    # + rail (accent) and GND rail
    out.append(wire(160, P, 470, P, ACCENT, 1.6))
    out.append(wire(494, P, 640, P, ACCENT, 1.6))
    out.append(wire(160, G, 640, G, MUTED, 1.6))
    # shunts: TVS 330, C1 410, C2 556, C3 612
    for x in (330, 410, 556, 612):
        out.append(wire(x, P, x, G, MUTED, 1.2))
    # star stub to ESP32 ground
    out.append(wire(480, G, 480, 368, MUTED, 1.2))
    # motor wires
    for y in (116, 188, 232, 304):
        out.append(wire(900, y, 1040, y, INK, 1.4))
    for y in (152, 268):
        out.append(wire(1000, y, 1040, y, SOFT, 1.2))
    out.append(label(908, 96, 84, "A1 BLACK", INK))
    out.append(label(908, 168, 84, "A2 GREEN", INK))
    out.append(label(908, 212, 84, "B1 RED", INK))
    out.append(label(908, 284, 84, "B2 BLUE", INK))
    out.append(label(908, 146, 80, "NC YELLOW"))
    out.append(label(908, 262, 80, "NC WHITE"))
    out.append(label(488, 348, 92, "TO ESP32 GND"))
    # PSU
    out.append(node(40, 88, 120, 244, "PSU", "backend", 32))
    out.append(name(100, 200, "24 V PSU"))
    out.append(sub(100, 216, "M200 supply"))
    out.append(pin(148, P, "V+", "end"))
    out.append(pin(148, G, "V−", "end"))
    # series parts on + rail
    out.append(fuse(200, P))
    out.append(comp(200, 100, "F1 · 5 A T"))
    out.append(diode_h(272, P))
    out.append(comp(272, 88, "Q1 · ideal diode"))
    out.append(comp(272, 100, "LM74700 + MOSFET"))
    out.append(contact(470, 494, P))
    out.append(comp(482, 88, "K1 · E-stop relay"))
    out.append(comp(482, 100, "NO contact"))
    # shunt parts
    out.append(tvs_v(330, 210))
    out.append(cap_v(410, 210, True))
    out.append(cap_v(556, 210, True))
    out.append(cap_v(612, 210))
    for x in (330, 410, 556, 612):
        out.append(dot(x, P, ACCENT) + dot(x, G, MUTED))
    out.append(dot(480, G, ACCENT, 5))
    out.append(comp(330, 320, "D1 SMBJ26A"))
    out.append(comp(330, 332, "TVS"))
    out.append(comp(410, 320, "C1 1000 µF"))
    out.append(comp(410, 332, "35 V low-ESR"))
    out.append(comp(556, 320, "C2 100 µF"))
    out.append(comp(612, 332, "C3 100 nF"))
    out.append(comp(584, 344, "on the carrier", MUTED))
    out.append(comp(480, 288, "star", ACCENT))
    # carrier
    out.append(node(640, 72, 260, 276, "DRV", "focal"))
    out.append(name(770, 100, "TMC2209 on carrier"))
    out.append(pin(652, P, "VM", "start"))
    out.append(pin(652, G, "GND", "start"))
    for y, t in ((116, "A1"), (188, "A2"), (232, "B1"), (304, "B2")):
        out.append(pin(888, y, t, "end"))
    out.append(sub(770, 200, "VM 4.75 – 29 V"))
    out.append(sub(770, 216, "logic pins: figure 02"))
    # motor
    out.append(node(1040, 72, 200, 276, "MOT", "backend", 32))
    out.append(name(1176, 100, "6-wire stepper"))
    for y in (116, 152, 188, 232, 268, 304):
        out.append(wire(1040, y, 1076, y, INK, 1.2))
    for y0 in (108, 224):
        out.append(f'<rect x="1076" y="{y0}" width="12" height="88" rx="2" fill="{ink(0.06)}" stroke="{INK}" stroke-width="1.2"/>')
        for k in range(1, 6):
            out.append(f'<line x1="1076" y1="{y0+k*14.67:.1f}" x2="1088" y2="{y0+k*14.67:.1f}" stroke="{ink(0.35)}" stroke-width="0.8"/>')
    out.append(comp(1096, 156, "coil A", MUTED, "start"))
    out.append(comp(1096, 272, "coil B", MUTED, "start"))
    out.append(nema(1196, 210, 36))
    out.append(sub(1176, 336, "centre taps unused"))
    out.append(callout(40, 404, "Every motor wire ends at the driver; nothing from the motor goes to the supply."))
    out.append(callout(720, 404, "The centre taps stay unconnected and insulated."))
    out.append(legend(420, W, [
        ("box", "TMC2209 (focal)", (ACC_TINT, ACCENT, False)),
        ("line", "+24 V rail", (ACCENT, None, False)),
        ("line", "Ground / shunt", (MUTED, None, False)),
        ("line", "Motor coil wire", (INK, None, False)),
        ("line", "Unused centre tap", (SOFT, None, False)),
    ]))
    out.append('</g></svg>')
    return "".join(out)


# ---------- figure 2: logic wires + endstop ----------
def fig_logic():
    slug, W, H = "axis-logic", 1280, 588
    out = [svg_open(slug, W, H, "One axis: logic wires and endstop",
                    "ESP32-S3 GPIO 4, 5 and 8 reach STEP, DIR and EN through 220 ohm resistors with a 10k pull-up on EN; GPIO 9 reaches PDN_UART through 1k while GPIO 10 connects directly; 3V3 feeds VIO with a 100 nF capacitor; the X min switch reaches GPIO 1 through a 10k pull-up, 1k series resistor and 100 nF capacitor.")]
    rows = {"STEP": 104, "DIR": 152, "EN": 200, "TX": 248, "RX": 296, "3V3": 344, "GND": 392, "ES": 440}
    # wires
    for k in ("STEP", "DIR", "EN"):
        out.append(wire(240, rows[k], 760, rows[k]))
    out.append(wire(640, 200, 640, 168))
    out.append(wire(240, 248, 760, 248, LINK))
    out.append(f'<path d="M240,296 H672 Q680,296 680,288 V248" fill="none" stroke="{LINK}" stroke-width="1.2"/>')
    out.append(wire(240, 344, 760, 344))
    out.append(wire(240, 392, 760, 392))
    out.append(wire(600, 344, 600, 392))
    out.append(wire(240, 440, 760, 440))
    out.append(wire(320, 440, 320, 472))
    out.append(wire(600, 440, 600, 412))
    # glyphs
    for k, rn in (("STEP", "R1"), ("DIR", "R2"), ("EN", "R3")):
        out.append(res_h(448, rows[k]))
        out.append(comp(464, rows[k] - 12, f"{rn} 220 Ω"))
    out.append(res_v(640, 176) + bar(640, 168))
    out.append(comp(656, 180, "R4 10 kΩ", SOFT, "start"))
    out.append(comp(656, 192, "to 3V3", MUTED, "start"))
    out.append(res_h(448, 248))
    out.append(comp(464, 236, "R5 1 kΩ", LINK))
    out.append(dot(680, 248, LINK))
    out.append(comp(464, 284, "direct, no resistor", MUTED))
    out.append(cap_v(600, 368))
    out.append(dot(600, 344) + dot(600, 392))
    out.append(comp(616, 371, "C4 100 nF", SOFT, "start"))
    out.append(cap_v(320, 456) + ground(320, 472))
    out.append(dot(320, 440))
    out.append(comp(336, 470, "C5 100 nF", SOFT, "start"))
    out.append(res_h(448, 440))
    out.append(comp(464, 428, "R7 1 kΩ"))
    out.append(res_v(600, 416) + bar(600, 412))
    out.append(dot(600, 440))
    out.append(comp(616, 424, "R6 10 kΩ to 3V3", SOFT, "start"))
    # ESP32
    out.append(node(40, 40, 200, 440, "MCU", "backend"))
    out.append(esp32_chip(52, 64))
    out.append(name(160, 84, "ESP32-S3"))
    for t, k in (("GPIO 4 · STEP", "STEP"), ("GPIO 5 · DIR", "DIR"), ("GPIO 8 · EN", "EN"), ("GPIO 9 · TX1", "TX"),
                 ("GPIO 10 · RX1", "RX"), ("3V3", "3V3"), ("GND", "GND"), ("GPIO 1 · X MIN", "ES")):
        out.append(pin(228, rows[k], t, "end"))
    # driver
    out.append(node(760, 40, 220, 372, "DRV", "focal"))
    out.append(name(870, 76, "TMC2209 logic pins"))
    for t, k in (("STEP", "STEP"), ("DIR", "DIR"), ("EN", "EN"), ("PDN_UART", "TX"), ("VIO", "3V3"), ("GND", "GND")):
        out.append(pin(772, rows[k], t, "start"))
    out.append(sub(772, 292, "MS1 → GND", MUTED, "start"))
    out.append(sub(772, 306, "MS2 → GND  (address 0)", MUTED, "start"))
    out.append(sub(870, 186, "low = enabled", SOFT))
    # switch
    out.append(node(760, 424, 220, 56, "SW", "input"))
    out.append(name(870, 448, "X min switch"))
    out.append(sub(870, 464, "COM → this wire · NO → GND"))
    out.append(callout(40, 516, "TX goes through 1 kΩ so a driver's reply wins on the shared wire; RX just listens."))
    out.append(callout(720, 516, "220 Ω on each line limits the damage if a driver fails."))
    out.append(legend(528, W, [
        ("box", "TMC2209 (focal)", (ACC_TINT, ACCENT, False)),
        ("box", "Switch input", ("rgba(191,192,192,0.10)", SOFT, False)),
        ("line", "Logic wire 3.3 V", (MUTED, None, False)),
        ("line", "UART wire", (LINK, None, False)),
    ]))
    out.append('</svg>')
    return "".join(out)


EXTRA_CSS = f"""
table {{ width:100%; border-collapse:collapse; font-size:0.86rem; margin:0.5rem 0 1.5rem; }}
th {{ font-family:{MONO}; font-size:0.62rem; letter-spacing:0.14em; text-transform:uppercase; color:var(--soft); font-weight:500; text-align:left; padding:8px 10px; border-bottom:1px solid var(--rule); }}
td {{ padding:7px 10px; border-bottom:1px solid var(--rule); color:var(--muted); vertical-align:top; }}
td:first-child {{ color:var(--ink); font-weight:600; white-space:nowrap; }}
td code {{ font-family:{MONO}; color:var(--ink); font-size:0.8rem; }}
tr.group td {{ color:var(--soft); font-family:{MONO}; font-size:0.62rem; letter-spacing:0.16em; text-transform:uppercase; font-weight:500; padding-top:16px; }}
.table-wrap {{ overflow-x:auto; }}
.cards {{ grid-template-columns:1.2fr 1fr; }}
"""

NET = [
    ("Power, 24 V", None),
    ("+24 V", "PSU V+", "F1 5 A slow-blow → Q1 ideal diode → K1 relay contact", "Carrier VM", "0.75–1 mm² (AWG 18)"),
    ("Shunts on +24 V", "+24 V rail", "D1 SMBJ26A (cathode to +), C1 1000 µF 35 V", "GND rail", "short leads, at the rail entry"),
    ("Local caps", "Carrier VM", "C2 100 µF, C3 100 nF (usually already on the carrier)", "Carrier GND", "as close to the driver as possible"),
    ("Power GND", "Carrier GND", "star point", "PSU V−", "0.75–1 mm²"),
    ("Motor, 6 wires", None),
    ("Coil A end", "Motor black", "none", "TMC2209 A1", "0.25–0.5 mm², twisted with A2"),
    ("Coil A end", "Motor green", "none", "TMC2209 A2", "twisted with A1"),
    ("Coil A centre", "Motor yellow", "none", "not connected, insulate", "heat-shrink the end"),
    ("Coil B end", "Motor red", "none", "TMC2209 B1", "twisted with B2"),
    ("Coil B end", "Motor blue", "none", "TMC2209 B2", "twisted with B1"),
    ("Coil B centre", "Motor white", "none", "not connected, insulate", "heat-shrink the end"),
    ("Logic, 3.3 V", None),
    ("STEP", "GPIO 4", "R1 220 Ω", "STEP", ""),
    ("DIR", "GPIO 5", "R2 220 Ω", "DIR", ""),
    ("EN", "GPIO 8", "R3 220 Ω, R4 10 kΩ from EN to 3V3", "EN", "high = motor off during boot"),
    ("UART TX", "GPIO 9", "R5 1 kΩ", "PDN_UART", "shared by all four drivers"),
    ("UART RX", "GPIO 10", "direct", "PDN_UART", "joins after R5"),
    ("VIO", "ESP32 3V3", "C4 100 nF to GND at the driver", "VIO", ""),
    ("Logic GND", "ESP32 GND", "star point", "Driver GND", "one ground path only"),
    ("MS1 / MS2", "Carrier DIP", "GND / GND for X (see address table)", "MS1 / MS2", "MS3 switch off"),
    ("Endstop", None),
    ("X min", "Switch COM", "R6 10 kΩ to 3V3, R7 1 kΩ series, C5 100 nF at the GPIO to GND", "GPIO 1", "switch NO → GND; hit = LOW"),
]


def table():
    rows = []
    for r in NET:
        if r[1] is None:
            rows.append(f'<tr class="group"><td colspan="5">{r[0]}</td></tr>')
        else:
            rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    return "".join(rows)


CARDS = """
<div class="cards">
  <div class="card focal"><h3>Check the 6 wires before connecting</h3><ul>
    <li>Colours vary between makers, so measure. With a multimeter on Ω:</li>
    <li>End to end of one coil = <code>2R</code> (for example 4 Ω)</li>
    <li>Centre tap to either end of the same coil = <code>R</code> (half of that)</li>
    <li>Between coil A and coil B = open, no reading</li>
    <li>The two wires with the highest reading in each group of three are the ends. They go to A1/A2 or B1/B2</li>
    <li>If the motor turns the wrong way, swap A1 and A2, or invert DIR in firmware</li>
  </ul></div>
  <div class="card"><h3>If the connector has 6 pins but only 4 wires</h3><ul>
    <li>Many NEMA 17 motors use a 6-pin connector with pins 2 and 5 empty</li>
    <li>Then pins 1-3 are coil A and 4-6 are coil B; the wiring above still applies</li>
    <li>Never connect a centre tap to +24 V. That's unipolar wiring, which a TMC2209 can't drive</li>
    <li>Using the full coil means current is set lower; with half a coil it would be higher. Start at the current from the per-axis table</li>
  </ul></div>
</div>
"""


def page():
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>One-axis motor wiring</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&family=Noto+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>{CSS}{EXTRA_CSS}</style>
</head>
<body>
<div class="frame">
  <p class="eyebrow">Architecture · Schematic · Diagram Design</p>
  <h1>Wiring one stepper axis, wire by wire</h1>
  <p class="lede">Every wire for one M200 axis (X shown): the 24 V path into the TMC2209 with each fuse, diode and capacitor, the motor's six wires, and every logic wire from the ESP32-S3 with its resistor. The other three axes repeat this, sharing the same 24 V rail, star ground and UART wire.</p>

  <section>
    <h2><span>01</span>24 V path and the motor</h2>
    <p class="note">Power enters from the left and passes the fuse, the reverse-polarity ideal diode and the E-stop relay in series. The TVS and capacitors sit across the rail. The motor connects only to the driver's four outputs.</p>
    <div class="diagram-container">{fig_power()}</div>
  </section>

  <section>
    <h2><span>02</span>Logic wires and the endstop</h2>
    <p class="note">Everything here is 3.3 V. Each signal gets a series resistor; the endstop gets a pull-up, a series resistor and a filter capacitor so cable noise can't fake a hit.</p>
    <div class="diagram-container">{fig_logic()}</div>
  </section>

  <section>
    <h2><span>03</span>Every wire</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Wire</th><th>From</th><th>Through</th><th>To</th><th>Notes</th></tr></thead>
      <tbody>{table()}</tbody></table></div>
    {CARDS}
  </section>
</div>
</body>
</html>
"""


with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(page())
print("wrote", sys.argv[1])
