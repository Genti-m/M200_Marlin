import os, sys
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "wiring_helpers.py"), encoding="utf-8").read())


def zone(x, y, w, h, text, lw):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{ink(0.015)}" stroke="{ink(0.22)}" stroke-width="1" stroke-dasharray="5,4"/>'
            f'<rect x="{x+12}" y="{y-6}" width="{lw}" height="12" fill="{PAPER}"/>'
            f'<text x="{x+18}" y="{y+3}" fill="{SOFT}" font-size="8" font-family="{MONO}" letter-spacing="0.16em">{text}</text>')


def fig_power():
    slug, W, H = "m200-motor-power", 1280, 496
    out = [svg_open(slug, W, H, "Motor power path with protections",
                    "Mains feeds the 24 V supply; a protection stage with fuse, reverse-polarity ideal diode, TVS and bulk capacitor feeds an E-stop controlled VM switch to four TMC2209 drivers and motors, and a fused buck converter powers the ESP32-S3, which enables the VM switch.")]
    out.append('<g transform="translate(0,-60)">')
    mk, acc = f"url(#{slug}-arrow)", f"url(#{slug}-arrow-accent)"
    cy = 180
    # main row arrows
    for x1, x2, col, m in ((168, 232, MUTED, mk), (380, 444, ACCENT, acc), (664, 728, ACCENT, acc), (880, 944, ACCENT, acc)):
        out.append(f'<line x1="{x1}" y1="{cy}" x2="{x2}" y2="{cy}" stroke="{col}" stroke-width="1.4" marker-end="{m}"/>')
    # protection -> buck
    out.append(f'<line x1="554" y1="280" x2="554" y2="344" stroke="{MUTED}" stroke-width="1.2" marker-end="{mk}"/>')
    # buck -> esp
    out.append(f'<line x1="664" y1="388" x2="728" y2="388" stroke="{MUTED}" stroke-width="1.2" marker-end="{mk}"/>')
    # esp -> switch enable
    out.append(f'<line x1="772" y1="344" x2="772" y2="240" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5,4" marker-end="{mk}"/>')
    # e-stop -> switch
    out.append(f'<path d="M944,372 H868 Q860,372 860,364 V240" fill="none" stroke="{MUTED}" stroke-width="1.2" marker-end="{mk}"/>')
    out.append(label(172, 160, 56, "230 VAC"))
    out.append(label(392, 160, 40, "24 V", ACCENT))
    out.append(label(676, 160, 40, "24 V", ACCENT))
    out.append(label(892, 160, 40, "VM", ACCENT))
    out.append(label(562, 306, 40, "24 V"))
    out.append(label(676, 368, 40, "5 V"))
    out.append(label(704, 286, 60, "ENABLE"))
    out.append(label(868, 380, 64, "NC LOOP"))
    # nodes
    out.append(node(40, 120, 128, 120, "AC", "input"))
    out.append(name(104, 166, "Mains inlet"))
    out.append(sub(104, 184, "IEC · switch · fuse"))
    out.append(sub(104, 198, "PE → frame + PSU"))
    out.append(node(232, 120, 148, 120, "PSU", "backend", 32))
    out.append(name(306, 166, "M200 PSU"))
    out.append(sub(306, 184, "24 V DC · trim 24.0"))
    out.append(sub(306, 198, "check the label"))
    out.append(node(444, 100, 220, 180, "PROT", "focal", 36))
    out.append(name(554, 134, "Protection stage"))
    rows = ["F1 5 A slow · VM branch", "F2 1 A poly · logic", "ideal diode · reverse pol.", "TVS SMBJ26A · spikes", "1000 µF 35 V low-ESR", "star ground point"]
    for i, r in enumerate(rows):
        out.append(sub(554, 156 + i * 18, r, MUTED))
    out.append(node(728, 120, 152, 120, "SW", "backend"))
    out.append(name(804, 160, "VM switch"))
    out.append(sub(804, 178, "relay or high-side"))
    out.append(sub(804, 192, "MOSFET, off = 0 V"))
    out.append(sub(804, 206, "needs both inputs"))
    out.append(node(944, 96, 296, 168, "DRV", "backend"))
    out.append(name(1092, 122, "4× TMC2209 + motors"))
    for i, ax in enumerate("XYZE"):
        tx = 960 + i * 70
        out.append(f'<rect x="{tx}" y="136" width="58" height="84" rx="4" fill="{ink(0.03)}" stroke="{ink(0.25)}" stroke-width="1"/>')
        out.append(sub(tx + 29, 152, ax, INK))
        out.append(nema(tx + 29, 180, 24))
        out.append(sub(tx + 29, 210, "100 µF"))
    out.append(sub(1092, 244, "+100 nF at each VM pin"))
    out.append(node(444, 344, 220, 88, "DC", "backend"))
    out.append(name(554, 374, "Buck 24 → 5 V"))
    out.append(sub(554, 392, "≥ 2 A · fused on F2"))
    out.append(sub(554, 406, "Schottky if USB too"))
    out.append(node(728, 344, 108, 88, "MCU", "backend"))
    out.append(esp32_chip(762, 366))
    out.append(sub(782, 410, "ESP32-S3"))
    out.append(sub(782, 424, "GPIO 8 EN"))
    out.append(node(944, 344, 136, 88, "STOP", "input", 36))
    out.append(name(1012, 378, "E-stop button"))
    out.append(sub(1012, 396, "mushroom · NC"))
    out.append(sub(1012, 410, "cuts VM in hardware"))
    out.append(callout(40, 476, "The motors lose power even if the firmware hangs: the button sits in the switch's loop, not in code."))
    out.append(legend(500, W, [
        ("box", "Protection stage (focal)", (ACC_TINT, ACCENT, False)),
        ("box", "Power / logic part", (PAPER2, INK, False)),
        ("box", "Outside input", ("rgba(191,192,192,0.10)", SOFT, False)),
        ("line", "Motor power (24 V)", (ACCENT, f"{slug}-arrow-accent", False)),
        ("line", "Supply", (MUTED, f"{slug}-arrow", False)),
        ("line", "Control signal", (MUTED, f"{slug}-arrow", True)),
    ]))
    out.append('</g></svg>')
    return "".join(out)


def fig_shared():
    slug, W, H = "m200-shared-signals", 1280, 472
    out = [svg_open(slug, W, H, "What the logic and power sides share",
                    "The ESP32-S3 on the 3.3 V logic side shares STEP and DIR, enable, UART, VIO and ground with the TMC2209 drivers, which bridge to the 24 V power side; all grounds meet at one star point, and the USB link to a PC can be isolated.")]
    mk, lk, acc = f"url(#{slug}-arrow)", f"url(#{slug}-arrow-link)", f"url(#{slug}-arrow-accent)"
    out.append(zone(24, 48, 794, 220, "LOGIC SIDE · 3V3", 120))
    out.append(zone(842, 48, 414, 220, "POWER SIDE · 24 V", 128))
    # USB
    out.append(f'<line x1="180" y1="148" x2="260" y2="148" stroke="{LINK}" stroke-width="1.2" stroke-dasharray="5,4" marker-end="{lk}"/>')
    # ESP -> driver signals
    sig = [(100, "STEP·DIR 220Ω", MUTED, mk), (132, "EN 10K PULL-UP", MUTED, mk), (164, "UART 1KΩ", LINK, lk), (196, "VIO 3V3", MUTED, mk)]
    for y, _, col, m in sig:
        out.append(f'<line x1="460" y1="{y}" x2="720" y2="{y}" stroke="{col}" stroke-width="1.2" marker-end="{m}"/>')
    # PSU -> driver VM
    out.append(f'<line x1="1020" y1="148" x2="940" y2="148" stroke="{ACCENT}" stroke-width="1.4" marker-end="{acc}"/>')
    # grounds to star
    for x in (360, 880, 1120):
        out.append(f'<line x1="{x}" y1="224" x2="{x}" y2="320" stroke="{MUTED}" stroke-width="1.2" marker-end="{mk}"/>')
    out.append(label(196, 128, 48, "USB", LINK))
    for y, t, col, _ in sig:
        out.append(label(500, y - 20, 116, t, LINK if col == LINK else SOFT))
    out.append(label(952, 128, 56, "VM 24 V", ACCENT))
    out.append(label(368, 268, 52, "GND"))
    out.append(label(888, 280, 52, "GND"))
    out.append(label(1128, 268, 72, "PSU −"))
    # nodes
    out.append(node(40, 104, 140, 88, "HOST", "optional", 32))
    out.append(name(110, 136, "PC / host"))
    out.append(sub(110, 154, "USB isolator"))
    out.append(sub(110, 168, "ADuM3160, optional"))
    out.append(node(260, 72, 200, 152, "MCU", "backend"))
    out.append(esp32_chip(340, 96))
    out.append(name(360, 152, "ESP32-S3"))
    out.append(sub(360, 170, "3.3 V GPIO only"))
    out.append(sub(360, 184, "5 V in from buck"))
    out.append(sub(360, 198, "never touches 24 V"))
    out.append(node(720, 72, 220, 152, "DRV", "backend"))
    out.append(name(830, 100, "4× TMC2209"))
    out.append(sub(776, 124, "logic pins", SOFT))
    out.append(sub(776, 140, "STEP DIR EN"))
    out.append(sub(776, 154, "PDN_UART VIO"))
    out.append(sub(886, 124, "power pins", SOFT))
    out.append(sub(886, 140, "VM · GND"))
    out.append(sub(886, 154, "A1 A2 B1 B2"))
    out.append(f'<line x1="830" y1="112" x2="830" y2="164" stroke="{ink(0.18)}" stroke-width="1"/>')
    out.append(sub(830, 200, "the only bridge"))
    out.append(node(1020, 72, 200, 152, "PSU", "backend", 32))
    out.append(name(1120, 120, "24 V after protection"))
    out.append(sub(1120, 140, "fuse · diode · TVS"))
    out.append(sub(1120, 154, "1000 µF bulk"))
    out.append(node(260, 320, 960, 56, "GND", "focal", 32))
    out.append(name(740, 346, "Star ground point · at the protection board"))
    out.append(sub(740, 364, "heavy motor return goes straight to PSU −; logic ground joins at one point only"))
    out.append(callout(40, 412, "Motor current never returns through the ESP32 or the USB cable."))
    out.append(callout(640, 412, "Logic lines carry only 3.3 V signals; a 220 Ω resistor limits damage if a driver fails."))
    out.append(legend(424, W, [
        ("box", "Star ground (focal)", (ACC_TINT, ACCENT, False)),
        ("box", "Optional", (ink(0.02), ink(0.30), True)),
        ("line", "Logic signal", (MUTED, f"{slug}-arrow", False)),
        ("line", "UART / USB", (LINK, f"{slug}-arrow-link", False)),
        ("line", "Motor power", (ACCENT, f"{slug}-arrow-accent", False)),
    ]))
    out.append('</svg>')
    return "".join(out)


EXTRA_CSS = f"""
table {{ width:100%; border-collapse:collapse; font-size:0.86rem; margin:0.5rem 0 1.5rem; }}
th {{ font-family:{MONO}; font-size:0.62rem; letter-spacing:0.14em; text-transform:uppercase; color:var(--soft); font-weight:500; text-align:left; padding:8px 10px; border-bottom:1px solid var(--rule); }}
td {{ padding:8px 10px; border-bottom:1px solid var(--rule); color:var(--muted); vertical-align:top; }}
td:first-child {{ color:var(--ink); font-weight:600; white-space:nowrap; }}
td code, li code {{ font-family:{MONO}; color:var(--ink); font-size:0.8rem; }}
.table-wrap {{ overflow-x:auto; }}
"""

PROT = [
    ("1 · Mains", "IEC inlet with switch and fuse, PE wired to frame and PSU", "Keep the stock inlet", "Switch power on the mains side, never by plugging the 24 V connector"),
    ("2 · Branch fuses", "F1 slow-blow on the motor branch, F2 polyfuse on the logic branch", "<code>5 A</code> T-fuse, <code>1 A</code> PTC", "A short becomes a blown fuse, not a burnt harness. Heaters later get their own fuse"),
    ("3 · Reverse polarity", "Ideal-diode controller with N-MOSFET, or a P-MOSFET", "<code>LM74700-Q1</code>", "Swapped 24 V wires would kill all four drivers; a plain diode wastes about 1 W"),
    ("4 · Overvoltage", "TVS diode across the rail, next to the bulk capacitor", "<code>SMBJ26A</code>", "TMC2209 VM is 29 V maximum. Also catches back-EMF when axes are pushed by hand"),
    ("5 · Bulk capacitor", "Low-ESR electrolytic at the rail entry, plus 100 µF and 100 nF at each driver", "<code>1000 µF 35 V</code>", "Absorbs the energy motors push back when braking and keeps VM steady"),
    ("6 · E-stop", "NC mushroom button in series with the relay coil or MOSFET gate; ESP32 must also enable", "Relay ≥ 10 A or high-side MOSFET", "Stops motion in hardware even if firmware hangs"),
    ("7 · Logic supply", "Buck 24 → 5 V on F2; Schottky diode if USB also feeds the board", "≥ <code>2 A</code> module", "USB and buck never back-feed each other"),
    ("8 · Signal lines", "220 Ω series on STEP, DIR, EN; RC 1 kΩ / 100 nF on endstops", "0603 resistors", "A failed driver or noisy cable can't reach the ESP32 pins at full current"),
    ("9 · Connectors", "Locking connectors on motors and VM", "JST-VH, Micro-Fit", "Unplugging a motor while VM is live destroys the TMC2209"),
    ("10 · USB to PC", "USB isolator between PC and ESP32", "<code>ADuM3160</code>, optional", "Breaks the ground loop through the PC, so the ESP32 doesn't reset under motor load"),
]


def table():
    rows = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in PROT)
    return f"""
  <section>
    <h2><span>03</span>Protections, in order from the wall</h2>
    <p class="note">Everything from step 2 to step 5 sits on one small board between the PSU and the drivers. Expect about 2 A on the motor branch with four motors at 0.8 A; 5 A leaves room for acceleration peaks.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Stage</th><th>What</th><th>Example part</th><th>Why</th></tr></thead>
      <tbody>{rows}</tbody></table></div>
  </section>
"""


def page():
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>M200 motor power</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&family=Noto+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>{CSS}{EXTRA_CSS}</style>
</head>
<body>
<div class="frame">
  <p class="eyebrow">Architecture · Power · Diagram Design</p>
  <h1>Powering the M200 motors safely</h1>
  <p class="lede">How 24 V reaches the four TMC2209 drivers and motors once the Zortrax board is gone, what passes between the ESP32-S3 and the drivers, and which protections go in front of the power supply.</p>

  <section>
    <h2><span>01</span>Power path</h2>
    <p class="note">Motor power goes through one protection stage and one switch before it reaches the drivers. The ESP32 gets its own fused 5 V branch, so a motor fault can't take the controller down with it.</p>
    <div class="diagram-container">{fig_power()}</div>
  </section>

  <section>
    <h2><span>02</span>What the two sides share</h2>
    <p class="note">The TMC2209 is the only part that touches both sides. The ESP32 shares just 3.3 V signals and one ground reference with it, never the 24 V rail.</p>
    <div class="diagram-container">{fig_shared()}</div>
  </section>
{table()}
</div>
</body>
</html>
"""


with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(page())
print("wrote", sys.argv[1])
