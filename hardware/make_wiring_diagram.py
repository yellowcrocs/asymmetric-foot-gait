"""Render hardware/wiring.png — one-foot FSR wiring diagram."""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "hardware" / "wiring.png"
OUTLINE = ROOT / "assets" / "insole_outline.csv"

INK = "#28323c"
WIRE = "#39424c"
ESP_FILL = "#eaf3fb"
ESP_EDGE = "#2c5d8f"
FSR_FILL = "#fff1dc"
FSR_EDGE = "#b8791f"
RES_FILL = "#f1f1f1"
RES_EDGE = "#5a5a5a"
RAIL = "#2e8b57"
GND = "#4a4a4a"
NODE = "#d9534f"
PANEL = "#f7f9fb"
PANEL_EDGE = "#b9c3cd"


def wire(ax, points, color=WIRE, lw=2.0, z=1):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    ax.plot(xs, ys, color=color, lw=lw, solid_capstyle="round",
            solid_joinstyle="round", zorder=z)


def box(ax, x, y, w, h, fc, ec, lw=1.5, z=2, rounding=0.06):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.02,rounding_size={rounding}",
        fc=fc, ec=ec, lw=lw, zorder=z,
    )
    ax.add_patch(patch)
    return patch


def dot(ax, x, y, color=NODE, r=0.085, z=6):
    ax.add_patch(Circle((x, y), r, fc=color, ec="#6b1d1a", lw=1.0, zorder=z))


def label(ax, x, y, text, size=10, weight="normal", color=INK,
          ha="center", va="center", z=7):
    ax.text(x, y, text, fontsize=size, fontweight=weight, color=color,
            ha=ha, va=va, zorder=z)


def load_outline():
    pts = []
    with open(OUTLINE, newline="") as handle:
        for row in csv.reader(handle):
            if len(row) >= 2:
                try:
                    pts.append((float(row[0]), float(row[1])))
                except ValueError:
                    pass
    return np.array(pts)


fig, ax = plt.subplots(figsize=(14, 9))
fig.patch.set_facecolor("white")
ax.set_xlim(0, 14)
ax.set_ylim(0, 9)
ax.set_aspect("equal")
ax.axis("off")
fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)

label(ax, 7.0, 8.75, "FootGait — one-foot FSR wiring", 19, "bold")
label(ax, 7.0, 8.33,
      "4× FSR402 + 10 kΩ divider → ADC1   •   ESP32-WROOM-32   •   powerbank 5 V",
      11, color="#54616e")

# ESP32 board
box(ax, 0.55, 3.55, 3.10, 4.05, ESP_FILL, ESP_EDGE, lw=2.0)
label(ax, 2.10, 7.27, "ESP32-WROOM-32", 12, "bold")
label(ax, 2.10, 6.98, "WiFi + ADC1", 9, color="#54616e")

pins = [
    ("3V3", 6.88),
    ("GPIO34", 6.25),
    ("GPIO35", 5.78),
    ("GPIO32", 5.31),
    ("GPIO33", 4.84),
    ("GND", 4.30),
]
for name, y in pins:
    label(ax, 3.48, y, name, 10, "bold", ha="right")
    wire(ax, [(3.65, y), (3.86, y)], lw=1.6)

label(ax, 2.10, 3.86, "VIN (5V)", 9, "bold")
label(ax, 2.10, 3.66, "GND", 8, color="#54616e")

# Powerbank
box(ax, 0.80, 1.55, 2.60, 1.05, "#f0f7ee", "#4e7a4a", lw=1.6)
label(ax, 2.10, 2.33, "Powerbank 5 V", 11, "bold")
label(ax, 2.10, 2.02, "steady 5 V USB output", 8, color="#54616e")
ax.annotate("", xy=(2.05, 3.55), xytext=(2.05, 2.60),
            arrowprops=dict(arrowstyle="-|>", lw=2, color=WIRE))
ax.annotate("", xy=(2.75, 3.55), xytext=(2.75, 2.60),
            arrowprops=dict(arrowstyle="-|>", lw=2, color=WIRE))
label(ax, 1.62, 3.08, "5V → VIN", 8, color="#54616e", ha="right")
label(ax, 3.18, 3.08, "GND", 8, color="#54616e", ha="left")

# 3V3 / GND routing to the divider
wire(ax, [(3.86, 6.88), (6.60, 6.88), (6.60, 7.02)], color=RAIL, lw=2.4)
label(ax, 5.15, 7.03, "3V3", 10, "bold", color=RAIL)
wire(ax, [(3.86, 4.30), (4.25, 4.30), (4.25, 3.30), (6.60, 3.30)],
     color=GND, lw=2.4)
label(ax, 5.15, 3.42, "GND", 10, "bold", color=GND, ha="center")

# Divider detail
dot(ax, 6.60, 7.02, color=RAIL, r=0.075)
wire(ax, [(6.60, 7.02), (6.60, 6.22)])
box(ax, 5.78, 5.66, 1.64, 0.56, FSR_FILL, FSR_EDGE, lw=1.8)
label(ax, 6.60, 5.94, "FSR402", 11, "bold", color="#8a5a14")
label(ax, 6.60, 5.47, "heel / ball-in / ball-out / toes", 8, color="#8a5a14")
wire(ax, [(6.60, 5.66), (6.60, 4.95)])
dot(ax, 6.60, 4.95)
label(ax, 6.60, 5.12, "Vout", 8, color="#6b1d1a")

wire(ax, [(6.60, 4.95), (6.60, 4.58)])
box(ax, 6.23, 3.98, 0.74, 0.60, RES_FILL, RES_EDGE, lw=1.6)
label(ax, 6.60, 4.28, "10 kΩ", 10, "bold")
wire(ax, [(6.60, 3.98), (6.60, 3.30)])
dot(ax, 6.60, 3.30, color=GND, r=0.075)

wire(ax, [(6.60, 4.95), (8.25, 4.95)], lw=2.0)
ax.annotate("", xy=(8.45, 4.95), xytext=(8.25, 4.95),
            arrowprops=dict(arrowstyle="-|>", lw=2, color=WIRE))
label(ax, 8.55, 4.95, "ADC1", 9, "bold", ha="left")

# Pin table
box(ax, 9.15, 5.02, 4.30, 2.55, PANEL, PANEL_EDGE, lw=1.4)
label(ax, 11.30, 7.32, "FSR → ESP32 pin", 12, "bold")
label(ax, 9.40, 6.99, "position", 9, "bold", color="#54616e", ha="left")
label(ax, 11.95, 6.99, "pin", 9, "bold", color="#54616e", ha="left")
rows = [
    ("heel", "GPIO34"),
    ("ball - inner", "GPIO35"),
    ("ball - outer", "GPIO32"),
    ("toes", "GPIO33"),
]
for i, (pos, pin) in enumerate(rows):
    y = 6.62 - i * 0.42
    label(ax, 9.40, y, pos, 10, ha="left")
    label(ax, 11.95, y, pin, 10, "bold", ha="left", color=ESP_EDGE)

# Placement inset
box(ax, 9.15, 1.50, 4.30, 3.30, "#fcfaf7", "#c9b79f", lw=1.4)
label(ax, 11.30, 4.57, "sensor placement", 12, "bold")
outline = load_outline()
minx, maxx = outline[:, 0].min(), outline[:, 0].max()
miny, maxy = outline[:, 1].min(), outline[:, 1].max()
foot_h = 2.35
foot_w = foot_h * (maxx - minx) / (maxy - miny)
fx = 10.20 - foot_w / 2.0
fy = 1.78 + (2.55 - foot_h) / 2.0
coords = []
for x, y in outline:
    cx = fx + (x - minx) * foot_w / (maxx - minx)
    cy = fy + (maxy - y) * foot_h / (maxy - miny)
    coords.append((cx, cy))
ax.add_patch(Polygon(coords, closed=True, fc="#f3e7d7", ec="#9c7b55",
                     lw=1.6, zorder=3))

sensors = [
    ("toes", 0.45, 0.10, "#d9534f"),
    ("ball - inner", 0.37, 0.30, "#2b7bbb"),
    ("ball - outer", 0.64, 0.30, "#2e8b57"),
    ("heel", 0.55, 0.86, "#b8791f"),
]
for name, nx, ny, color in sensors:
    cx = fx + nx * foot_w
    cy = fy + (1.0 - ny) * foot_h
    ax.add_patch(Circle((cx, cy), 0.075, fc=color, ec="white", lw=1.2, zorder=6))
    label(ax, fx + foot_w + 0.20, cy + (0.10 if name == "ball - inner" else 0.0),
          name, 8, ha="left", color=color)

# Notes
box(ax, 0.55, 0.30, 12.90, 1.05, "#fffbe6", "#d9c77a", lw=1.4)
notes = [
    "Repeat the divider 4× — one per FSR. The 3V3 and GND rails are shared.",
    "Use only ADC1 pins (GPIO32–35 / 36 / 39). ADC2 is unavailable while WiFi is on.",
    "Never apply 5 V to an ADC pin. FSRs are non-polarised. Same wiring on the other foot.",
]
for i, text in enumerate(notes):
    label(ax, 0.75, 1.13 - i * 0.30, text, 9, ha="left", color="#4d4a2f")

OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, dpi=200, facecolor="white")
print(f"wrote {OUT}")
