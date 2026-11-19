"""Generate the M200 V4 to ESP32-S3 + 4x TMC2209 wiring page (diagram-design, dark skin)."""
import sys

PAPER, PAPER2 = "#2d3142", "#393e53"
INK, MUTED, SOFT = "#f5f5f5", "#bfc0c0", "#949eb2"
ACCENT, ACC_TINT, LINK = "#f08a59", "rgba(240,138,89,0.10)", "#739fdf"
SANS = "'Geist', system-ui, sans-serif"
MONO = "'Geist Mono', ui-monospace, monospace"
SERIF = "'Instrument Serif', 'Noto Serif', serif"


def ink(a):
    return f"rgba(245,245,245,{a})"


ICONS = {
    "lock": '<path d="M5 13a2 2 0 0 1 2 -2h10a2 2 0 0 1 2 2v6a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-6" /> <path d="M11 16a1 1 0 1 0 2 0a1 1 0 0 0 -2 0" /> <path d="M8 11v-4a4 4 0 1 1 8 0v4" />',
    "git-branch": '<path d="M5 18a2 2 0 1 0 4 0a2 2 0 1 0 -4 0" /> <path d="M5 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0" /> <path d="M15 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0" /> <path d="M7 8l0 8" /> <path d="M9 18h6a2 2 0 0 0 2 -2v-5" /> <path d="M14 14l3 -3l3 3" />',
    "api": '<path d="M7 4a2 2 0 0 0 -2 2v3a2 3 0 0 1 -2 3a2 3 0 0 1 2 3v3a2 2 0 0 0 2 2" /> <path d="M17 4a2 2 0 0 1 2 2v3a2 3 0 0 0 2 3a2 3 0 0 0 -2 3v3a2 2 0 0 1 -2 2" />',
    "sync": '<path d="M20 11a8.1 8.1 0 0 0 -15.5 -2m-.5 -4v4h4" /> <path d="M4 13a8.1 8.1 0 0 0 15.5 2m.5 4v-4h-4" />',
    "laptop": '<path d="M3 19l18 0" /> <path d="M5 7a1 1 0 0 1 1 -1h12a1 1 0 0 1 1 1v8a1 1 0 0 1 -1 1h-12a1 1 0 0 1 -1 -1l0 -8" />',
    "alert": '<path d="M12 9v4" /> <path d="M10.363 3.591l-8.106 13.534a1.914 1.914 0 0 0 1.636 2.871h16.214a1.914 1.914 0 0 0 1.636 -2.87l-8.106 -13.536a1.914 1.914 0 0 0 -3.274 0" /> <path d="M12 16h.01" />',
}


def icon(name, x, y, color, scale=1.0):
    return (f'<g transform="translate({x},{y}) scale({scale})" color="{color}">'
            f'<svg aria-hidden="true" width="24" height="24" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
            f'{ICONS[name]}</svg></g>')


def markers():
    return (f'<defs>'
            f'<marker id="{{p}}arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{MUTED}"/></marker>'
            f'<marker id="{{p}}arrow-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{ACCENT}"/></marker>'
            f'<marker id="{{p}}arrow-link" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{LINK}"/></marker>'
            f'</defs>')


def svg_open(slug, w, h, title, desc):
    return (f'<svg viewBox="0 0 {w} {h}" style="min-width:{w}px" xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-labelledby="{slug}-title {slug}-desc">'
            f'<title id="{slug}-title">{title}</title><desc id="{slug}-desc">{desc}</desc>'
            + markers().replace("{p}", slug + "-")
            + f'<rect width="100%" height="100%" fill="{PAPER}"/>')


def node(x, y, w, h, tag, kind, tag_w=28):
    fills = {
        "focal": (ACC_TINT, ACCENT, "rgba(240,138,89,0.50)", ACCENT),
        "backend": (PAPER2, INK, ink(0.40), INK),
        "input": ("rgba(191,192,192,0.10)", SOFT, "rgba(148,158,178,0.50)", SOFT),
        "optional": (ink(0.02), ink(0.30), ink(0.22), SOFT),
    }
    fill, stroke, tstroke, tcolor = fills[kind]
    dash = ' stroke-dasharray="4,3"' if kind == "optional" else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{PAPER}"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"{dash}/>'
            f'<rect x="{x+8}" y="{y+6}" width="{tag_w}" height="12" rx="2" fill="transparent" stroke="{tstroke}" stroke-width="0.8"/>'
            f'<text x="{x+8+tag_w/2}" y="{y+15}" fill="{tcolor}" font-size="7" font-family="{MONO}" text-anchor="middle" letter-spacing="0.08em">{tag}</text>')


def name(cx, y, text, color=INK, size=12):
    return f'<text x="{cx}" y="{y}" fill="{color}" font-size="{size}" font-weight="600" font-family="{SANS}" text-anchor="middle">{text}</text>'


def sub(cx, y, text, color=MUTED, anchor="middle"):
    return f'<text x="{cx}" y="{y}" fill="{color}" font-size="9" font-family="{MONO}" text-anchor="{anchor}">{text}</text>'


def label(x, y, w, text, color=SOFT):
    """Arrow label: mask rect at (x,y) w x 12, text centered."""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="12" rx="2" fill="{PAPER}"/>'
            f'<text x="{x+w/2}" y="{y+9}" fill="{color}" font-size="8" font-family="{MONO}" text-anchor="middle" letter-spacing="0.06em">{text}</text>')


def callout(x, y, text):
    return f'<text x="{x}" y="{y}" fill="{SOFT}" font-size="14" font-style="italic" font-family="{SERIF}">{text}</text>'


def legend(y, w, items):
    out = [f'<line x1="40" y1="{y}" x2="{w-40}" y2="{y}" stroke="{ink(0.10)}" stroke-width="0.8"/>',
           f'<text x="40" y="{y+16}" fill="{MUTED}" font-size="8" font-family="{MONO}" letter-spacing="0.18em">LEGEND</text>']
    x = 40
    for kind, text, style in items:
        if kind == "box":
            fill, stroke, dash = style
            d = ' stroke-dasharray="4,3"' if dash else ""
            out.append(f'<rect x="{x}" y="{y+28}" width="14" height="10" rx="2" fill="{fill}" stroke="{stroke}" stroke-width="1"{d}/>')
        else:
            stroke, mk, dash = style
            d = ' stroke-dasharray="4,3"' if dash else ""
            m = f' marker-end="url(#{mk})"' if mk else ""
            out.append(f'<line x1="{x}" y1="{y+33}" x2="{x+28}" y2="{y+33}" stroke="{stroke}" stroke-width="1.2"{d}{m}/>')
        tx = x + (20 if kind == "box" else 36)
        out.append(f'<text x="{tx}" y="{y+36}" fill="{MUTED}" font-size="8.5" font-family="{SANS}">{text}</text>')
        x = tx + len(text) * 5.2 + 28
    return "".join(out)


def nema(cx, cy, s=28, color=MUTED):
    h = s / 2
    r_hole = 1.5 if s >= 24 else 1
    o = s * 0.32
    holes = "".join(f'<circle cx="{cx+dx}" cy="{cy+dy}" r="{r_hole}" fill="{color}"/>'
                    for dx in (-o, o) for dy in (-o, o))
    return (f'<g aria-hidden="true"><rect x="{cx-h}" y="{cy-h}" width="{s}" height="{s}" rx="3" fill="{ink(0.04)}" stroke="{color}" stroke-width="1"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{s*0.3}" fill="none" stroke="{color}" stroke-width="1"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{s*0.1}" fill="{color}"/>{holes}</g>')


def esp32_chip(x, y, color=INK):
    """Small ESP32 module glyph, 40 x 30: antenna meander + metal can."""
    meander = f"M{x+6},{y+9} V{y+3} H{x+11} V{y+9} H{x+16} V{y+3} H{x+21} V{y+9} H{x+26} V{y+3} H{x+31} V{y+9}"
    return (f'<g aria-hidden="true"><rect x="{x}" y="{y}" width="40" height="30" rx="2" fill="{ink(0.04)}" stroke="{color}" stroke-width="1"/>'
            f'<path d="{meander}" fill="none" stroke="{SOFT}" stroke-width="1"/>'
            f'<rect x="{x+4}" y="{y+12}" width="32" height="15" rx="1" fill="{ink(0.06)}" stroke="{color}" stroke-width="0.8"/>'
            f'<text x="{x+20}" y="{y+22}" fill="{color}" font-size="6" font-family="{MONO}" text-anchor="middle" letter-spacing="0.04em">ESP32</text></g>')


def esp32_devkit(x, y):
    """ESP32-S3 DevKit drawing, board 160 x 148 at (x, y)."""
    o = [f'<g aria-hidden="true">',
         f'<rect x="{x}" y="{y}" width="160" height="148" rx="4" fill="{ink(0.04)}" stroke="{ink(0.50)}" stroke-width="1"/>']
    for i in range(10):
        py = y + 18 + i * 12
        o.append(f'<circle cx="{x+10}" cy="{py}" r="2" fill="{ink(0.45)}"/>')
        o.append(f'<circle cx="{x+150}" cy="{py}" r="2" fill="{ink(0.45)}"/>')
    mx, my = x + 26, y + 8
    o.append(f'<rect x="{mx}" y="{my}" width="108" height="84" rx="2" fill="{ink(0.03)}" stroke="{ink(0.60)}" stroke-width="1"/>')
    meander = f"M{mx+10},{my+18} V{my+6}" + "".join(
        f" H{mx+10+8*(k+1)} V{my+(6 if k % 2 else 18)}" for k in range(10))
    o.append(f'<path d="{meander}" fill="none" stroke="{SOFT}" stroke-width="1"/>')
    o.append(f'<rect x="{mx+6}" y="{my+26}" width="96" height="52" rx="2" fill="{ink(0.07)}" stroke="{INK}" stroke-width="1"/>')
    o.append(f'<text x="{mx+54}" y="{my+50}" fill="{INK}" font-size="10" font-weight="600" font-family="{MONO}" text-anchor="middle">ESP32-S3</text>')
    o.append(f'<text x="{mx+54}" y="{my+64}" fill="{SOFT}" font-size="7" font-family="{MONO}" text-anchor="middle" letter-spacing="0.08em">WROOM-1</text>')
    o.append(f'<rect x="{x+34}" y="{y+108}" width="14" height="9" rx="2" fill="none" stroke="{ink(0.50)}" stroke-width="1"/>')
    o.append(f'<rect x="{x+112}" y="{y+108}" width="14" height="9" rx="2" fill="none" stroke="{ink(0.50)}" stroke-width="1"/>')
    o.append(f'<circle cx="{x+80}" cy="{y+112}" r="2.5" fill="{ACCENT}"/>')
    o.append(f'<rect x="{x+66}" y="{y+138}" width="28" height="12" rx="4" fill="{PAPER}" stroke="{ink(0.60)}" stroke-width="1"/>')
    o.append('</g>')
    return "".join(o)

CSS = f"""
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{ --paper:{PAPER}; --paper2:{PAPER2}; --ink:{INK}; --muted:{MUTED}; --soft:{SOFT}; --accent:{ACCENT}; --rule:rgba(245,245,245,0.12); }}
body {{ font-family:{SANS}; background:var(--paper); color:var(--ink); padding:3rem 2rem; }}
.frame {{ max-width:1280px; margin:0 auto; }}
.diagram-container {{ width:100%; overflow-x:auto; margin:0 0 1rem; }}
svg {{ width:100%; display:block; }}
.eyebrow {{ font-family:{MONO}; font-size:0.66rem; font-weight:500; letter-spacing:0.18em; text-transform:uppercase; color:var(--muted); margin-bottom:0.5rem; }}
h1 {{ font-family:{SERIF}; font-size:clamp(1.6rem, 2.4vw + 0.8rem, 2.4rem); font-weight:400; letter-spacing:-0.02em; line-height:1.15; margin-bottom:0.75rem; }}
.lede {{ color:var(--muted); max-width:62ch; line-height:1.55; margin-bottom:2.5rem; }}
section {{ margin-bottom:3rem; }}
h2 {{ font-family:{SERIF}; font-weight:400; font-size:1.5rem; margin-bottom:0.35rem; }}
h2 span {{ font-family:{MONO}; font-size:0.66rem; letter-spacing:0.18em; color:var(--soft); margin-right:0.6rem; vertical-align:middle; }}
.note {{ color:var(--muted); max-width:70ch; line-height:1.55; margin-bottom:1rem; font-size:0.95rem; }}
.cards {{ display:grid; grid-template-columns:1.1fr 1fr 0.9fr; gap:14px; }}
.card {{ border:1px solid var(--rule); border-radius:6px; padding:16px 18px; background:rgba(245,245,245,0.02); }}
.card h3 {{ font-size:0.95rem; font-weight:600; margin-bottom:0.6rem; display:flex; align-items:center; gap:8px; }}
.card h3::before {{ content:""; width:7px; height:7px; border-radius:50%; background:var(--soft); }}
.card.focal {{ border-color:rgba(240,138,89,0.5); }}
.card.focal h3::before {{ background:var(--accent); }}
.card ul {{ list-style:none; }}
.card li {{ color:var(--muted); font-size:0.88rem; line-height:1.5; padding:4px 0; border-top:1px solid var(--rule); }}
.card li:first-child {{ border-top:0; }}
.card code {{ font-family:{MONO}; font-size:0.8rem; color:var(--ink); }}
@media (max-width: 860px) {{ .cards {{ grid-template-columns:1fr; }} body {{ padding:2rem 1rem; }} }}
@media print {{ .diagram-container {{ overflow-x:visible; }} svg {{ min-width:0 !important; }} }}
"""
