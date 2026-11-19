# Web interface - visual options (milestone 4)

Updated: 2026-11-18
Goal: `http://m200.local` shows temps, Home / Motors off / Preheat / STOP buttons and a live console.
Choices made 2026-11-18 (see the **Choice** line in each grid). Built in `web/bridge-ui/`, messages in `docs/protocol.md`, PC mock `tools/mock_bridge.py`.

---

## Grid 1 - Page layout

```
 A) DASHBOARD CARDS                      B) PHONE-FIRST SINGLE COLUMN
┌──────────────────────────────────────┐ ┌───────────────────┐
│ M200  ● connected            [STOP]  │ │ M200  ●    [STOP] │
├────────────┬────────────┬────────────┤ ├───────────────────┤
│ HOTEND     │ BED        │ JOB        │ │ Hotend 215 / 215  │
│ 215 / 215  │  80 / 80   │ idle       │ │ Bed     80 / 80   │
├────────────┴────────────┼────────────┤ ├───────────────────┤
│ temp chart (10 min)     │ [Home]     │ │ [Home] [Motors off]│
│  ~~~~~~~~~~~~~~~~~~     │ [Motors off│ │ [Preheat ▼]       │
│                         │ [Preheat ▼]│ ├───────────────────┤
├─────────────────────────┴────────────┤ │ ▸ Console         │
│ console ...                    [send]│ │ ▸ SD files        │
└──────────────────────────────────────┘ └───────────────────┘
 + everything visible on a laptop        + great on phone, big touch targets
 - squeezed on phone (stacks to B)       - wastes space on desktop

 C) SIDEBAR APP (tabs)                   D) CONSOLE-FIRST (terminal style)
┌────────┬─────────────────────────────┐ ┌──────────────────────────────────────┐
│ M200   │ Dashboard                   │ │ T:215/215 B:80/80  idle   [STOP]     │
│        │ ┌─────────┐ ┌─────────┐     │ ├──────────────────────────────────────┤
│ ▣ Dash │ │ 215/215 │ │  80/80  │     │ │ > M105                               │
│ ▢ Ctrl │ └─────────┘ └─────────┘     │ │ ok T:215.0 /215.0 B:80.1 /80.0       │
│ ▢ Files│ chart ~~~~~~~~~~~~~~~~~     │ │ > G28                                │
│ ▢ Cons.│                             │ │ echo:busy: processing                │
│        │                             │ │ ok                                   │
│ [STOP] │                             │ ├──────────────────────────────────────┤
└────────┴─────────────────────────────┘ │ [Home][Motors off][Preheat] > ____   │
 + scales to milestone 6 (files, jobs)   └──────────────────────────────────────┘
 - one more click to reach things         + simplest, closest to Pronterface
                                          - not friendly for a quick glance
```

**Recommendation: A**, which collapses into B under 700 px wide. One page, no tabs. SD files (milestone 6) become one extra card.
**Choice: A (collapses to B on phones)**

---

## Grid 2 - Temperature display

```
 1) BIG NUMBERS                 2) NUMBERS + BAR               3) RING GAUGE
┌──────────────────────┐       ┌──────────────────────┐       ┌──────────────────────┐
│ HOTEND               │       │ HOTEND     215 / 215 │       │       ╭─────╮        │
│   215°               │       │ ████████████████░░░  │       │     ╱  215°  ╲       │
│   target 215         │       │ BED         62 / 80  │       │    │  /215    │      │
│                      │       │ ██████████░░░░░░░░░  │       │     ╲_______╱        │
└──────────────────────┘       └──────────────────────┘       └──────────────────────┘
 + readable from far away       + shows "how far to target"    + looks like an instrument
 - no sense of progress         - bar needs a fixed max        - takes the most space

 4) NUMBERS + LIVE CHART (history of the M155 reports)
┌──────────────────────────────────────────────────────────┐
│ ● Hotend 215/215   ● Bed 80/80                           │
│ 250┤              ╭──────────────── hotend               │
│    │          ╭───╯                                      │
│ 100┤    ╭─────┼──────────────────── bed                  │
│  20┼────╯     │                                          │
│    └────────────────────────────── last 10 min           │
└──────────────────────────────────────────────────────────┘
 + shows heating/stability problems   - ~300 lines of JS (hand-drawn canvas, no library)
```

**Recommendation: 2 + 4**: number with bar in the card, plus the chart under it (can be collapsed).
**Choice: 4: numbers + live chart**

---

## Grid 3 - Colour theme

| | Theme | Background | Text | Accent | STOP | Feel |
|---|---|---|---|---|---|---|
| **T1** | Dark workshop | `#16181d` | `#e6e6e6` | `#f0a020` amber | `#e5484d` red | calm, easy at night, printer-tool look |
| **T2** | Light clean | `#f6f7f9` | `#1c1e22` | `#2f6fdf` blue | `#d92d20` red | office / documentation look |
| **T3** | Zortrax-like | `#1e1e1e` | `#ffffff` | `#e30613` red | `#ffffff` on red | matches the printer, but red accent fights the STOP button |
| **T4** | Auto (T1 + T2) | follows the phone/PC dark-mode setting | | | | best of both, +~40 lines CSS |

Hot vs. cold colouring (any theme): below 50 °C grey, heating = accent, at target = green `#30a46c`.

**Recommendation: T4** (dark as default look).
**Choice: T4 (auto light/dark)**

---

## Grid 4 - Buttons / controls

```
 a) BUTTON ROW                  b) JOG PAD (later)             c) PRESET CHIPS
┌──────────────────────┐       ┌──────────────────────┐       ┌──────────────────────┐
│ [Home] [Motors off]  │       │       [Y+]     [Z+]  │       │ Preheat:             │
│ [Preheat ▼] [Cool]   │       │ [X-] [⌂] [X+]        │       │ (ABS 260/80)         │
│                      │       │       [Y-]     [Z-]  │       │ (Z-ULTRAT 255/80)    │
│ [ ■ STOP ]           │       │ step: 0.1 1 10 mm    │       │ (PLA 210/60) (Off)   │
└──────────────────────┘       └──────────────────────┘       └──────────────────────┘
 + minimum for milestone 4      + manual moves (G91/G1)         + one tap per material
                                - easy to crash the head        - presets need storage
```

STOP behaviour (any option): always visible in the header, red, asks "Stop the printer?" with one confirm tap.
Note: `Cap:EMERGENCY_PARSER:0`, so `M112` waits for the current command. The UI shows "STOP sent, waiting..." until Marlin reacts.

**Recommendation: a + c** now, b in milestone 6.
**Choice: b: jog pad (+ Home / Motors off on the pad, preheat by typing targets in the temp cards)**

---

## Grid 5 - Console

| | Option | What it does |
|---|---|---|
| **K1** | Raw | every line Marlin sends, including `ok` and the `T:` reports every 2 s |
| **K2** | Filtered (toggle) | hides `ok` and the auto temperature reports by default, a "show all" switch brings them back |
| **K3** | Filtered + history | K2 plus up/down arrow recalls commands, a small list of quick commands (`M105`, `M114`, `M115`, `M503`) |

Sent lines in accent colour (`> G28`), `Error:` lines red, `echo:` lines grey. Keep the last 500 lines.

**Recommendation: K3.**
**Choice: K3**

---

## Grid 6 - Status header

```
 H1) MINIMAL
┌──────────────────────────────────────────────────────────┐
│ M200 bridge   ● printer  ● wifi -52 dBm          [STOP]  │
└──────────────────────────────────────────────────────────┘
 H2) WITH STATE TEXT
┌──────────────────────────────────────────────────────────┐
│ M200 bridge   HEATING  hotend 182→215   ● ● ●    [STOP]  │
└──────────────────────────────────────────────────────────┘
```

Dots: printer link (gets replies), WebSocket to the browser, WiFi signal.
State words: OFFLINE, IDLE, HEATING, BUSY (`busy:` lines), PRINTING (milestone 6), ERROR.

**Recommendation: H2.**
**Choice: H1**

---

## Fixed technical choices (not visual, for reference)

- One `index.html` with inline CSS + JS, **no framework, no CDN** (the printer may be on a network without internet). Target < 40 kB gzipped, embedded in the firmware.
- Live data over one WebSocket `/ws`: the ESP32 pushes JSON (`{"t":"state",...}`, `{"t":"line",...}`), the browser sends commands.
- REST for scripts: `GET /api/state`, `POST /api/gcode`, `POST /api/stop`.
- A small PC mock server (`tools/`) fakes Marlin, so the page can be designed without flashing.
