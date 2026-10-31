"""FootGait live insole GUI.

Receives the ESP32 UDP stream, ACKs it, logs it, converts raw ADC to force,
and draws both insoles with glowing pressure dots. Run:

    python gui.py
    python gui.py --units raw
    python gui.py --units N --body-kg 70
    python gui.py --replay logs/gui_YYYYMMDD_HHMMSS.csv
    python gui.py --self-test
"""

import argparse
import csv
import math
import os
import socket
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

import logger as core
import processing as proc
import sensor_config as sc
from asymmetry_live import OnlineAsymmetry

BG = "#0b0f14"
FOOT_FILL = "#131a23"
FOOT_STROKE = "#34485e"
DIM = "#46586b"
TEXT = "#c9d6e5"
TITLE = "#8fa3b8"
ACCENT = "#7fd1ff"

ZERO_EPS = 20
TOP = 58
BOTTOM = 64

NODES = sc.NODES

SI_FIELDS = (
    "stance", "swing", "stride", "step", "peak", "impulse",
    "heel_share", "cop_path", "cop_ml_range",
)

OUTLINE_PATHS = [
    Path(__file__).resolve().parent / "assets" / "insole_outline.csv",
    Path(__file__).resolve().parent
    / "Brillnova-Insole"
    / "visualization"
    / "insole outline"
    / "insole_outline_px_smooth.csv",
]


def load_outline():
    for path in OUTLINE_PATHS:
        if not path.exists():
            continue
        pts = []
        with open(path, newline="") as handle:
            for row in csv.reader(handle):
                if len(row) >= 2:
                    try:
                        pts.append((float(row[0]), float(row[1])))
                    except ValueError:
                        pass
        if len(pts) >= 10:
            return pts
    raise SystemExit("insole outline not found (expected assets/insole_outline.csv)")


OUTLINE = load_outline()
MINX = min(p[0] for p in OUTLINE)
MAXX = max(p[0] for p in OUTLINE)
MINY = min(p[1] for p in OUTLINE)
MAXY = max(p[1] for p in OUTLINE)
XMID = (MINX + MAXX) / 2.0
W_OUT = MAXX - MINX
H_OUT = MAXY - MINY


def lerp(a, b, t):
    return a + (b - a) * t


def pressure_rgb(t):
    t = max(0.0, min(1.0, t))
    stops = [
        (0.00, (240, 248, 255)),
        (0.30, (255, 232, 150)),
        (0.60, (255, 130, 55)),
        (1.00, (255, 20, 20)),
    ]
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t <= t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return tuple(int(round(lerp(c0[k], c1[k], u))) for k in range(3))
    return stops[-1][1]


def blend(rgb, alpha):
    bg = (11, 15, 20)
    return tuple(int(round(lerp(bg[k], rgb[k], alpha))) for k in range(3))


def hexc(rgb):
    return "#%02x%02x%02x" % rgb


class FootGaitGUI:
    def __init__(self, root, port, no_log, self_test=False, replay=None,
                 units="N", body_kg=None, speed=1.0, calibration=None,
                 replay_start=0.0, asym_enabled=True, asym_window=12,
                 asym_threshold=3.0, color_full_raw=3000.0):
        self.root = root
        self.port = port
        self.no_log = no_log
        self.self_test = self_test
        self.units = units
        self.body_kg = body_kg
        self.start = time.time()
        self.last_flush = time.time()
        self.log_buf = []
        self.log_file = None
        self.log_dir = Path(__file__).resolve().parent / "logs"
        self.log_path = None
        self.layout = None
        self.sock = None
        self.sock_ok = False
        self.bind_error = None
        self.calib = proc.load_calibration(calibration)
        self.full_scale_n = float(self.calib.get("full_scale_n", proc.FSR_FULL_SCALE_N))
        self.color_full_raw = max(1.0, float(color_full_raw))
        self.legend_max = float(self.color_full_raw)
        self.legend_label = f"colour scale: raw ADC 0-{int(self.color_full_raw)}"
        self.asym_enabled = asym_enabled
        self.asym = (OnlineAsymmetry(self.calib, window=max(3, asym_window),
                                     threshold_n=asym_threshold)
                     if asym_enabled else None)
        self.asym_log_file = None
        self.asym_log_path = None
        self.last_asym_id = -1
        self.replay_path = replay
        self.replay_rows = []
        self.replay_idx = 0
        self.replay_t0 = 0.0
        self.replay_wall0 = 0.0
        self.replay_speed = max(0.01, speed)
        self.replay_start = max(0.0, replay_start)
        self.replay_done = False
        if replay:
            self.load_replay(replay)
            self.no_log = True

        root.title("FootGait - Live Insole Pressure")
        root.configure(bg=BG)
        root.geometry("1280x820")
        root.minsize(900, 600)

        top = tk.Frame(root, bg=BG)
        top.pack(fill="x")
        tk.Label(
            top, text="FootGait", bg=BG, fg=ACCENT, font=("Segoe UI", 15, "bold")
        ).pack(side="left", padx=(16, 8), pady=10)
        tk.Label(
            top, text="live insole pressure", bg=BG, fg=TITLE, font=("Segoe UI", 10)
        ).pack(side="left")

        self.stats_var = tk.StringVar(value="waiting for data...")
        tk.Label(
            top, textvariable=self.stats_var, bg=BG, fg=TEXT, font=("Consolas", 10)
        ).pack(side="right", padx=16)

        badge = tk.Frame(root, bg=BG)
        badge.pack(fill="x")
        self.asym_var = tk.StringVar(value="ASYMMETRY: collecting...")
        self.asym_detail_var = tk.StringVar(value="")
        self.asym_label = tk.Label(
            badge, textvariable=self.asym_var, bg=BG, fg=DIM,
            font=("Segoe UI", 11, "bold"),
        )
        self.asym_label.pack(side="left", padx=(16, 10), pady=(0, 7))
        tk.Label(
            badge, textvariable=self.asym_detail_var, bg=BG, fg=TITLE,
            font=("Consolas", 9),
        ).pack(side="left")
        if not asym_enabled:
            self.asym_var.set("ASYMMETRY: off")

        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_resize)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        if not self_test and not replay:
            self.setup_socket()
        self.setup_log()

        self.root.after(50, self.tick)

    def load_replay(self, path):
        rows = [(t, millis, side, vals) for t, millis, side, vals
                in proc.read_rows(path)]
        rows.sort(key=lambda item: item[0])
        self.replay_rows = rows
        if rows:
            self.replay_t0 = rows[0][0] + self.replay_start
            while (self.replay_idx < len(rows)
                   and rows[self.replay_idx][0] < self.replay_t0):
                self.replay_idx += 1
            self.replay_wall0 = time.time()

    def advance_replay(self, now):
        if not self.replay_rows or self.replay_done:
            return
        target = self.replay_t0 + (now - self.replay_wall0) * self.replay_speed
        latest = {}
        while self.replay_idx < len(self.replay_rows):
            t, millis, side, vals = self.replay_rows[self.replay_idx]
            if t > target:
                break
            if self.asym is not None:
                self.asym.add(side, vals, t, millis)
            latest[side] = vals
            self.replay_idx += 1
        for side, vals in latest.items():
            st = core.STATE[side]
            st["vals"] = vals
            st["last_t"] = now
            st["times"].append(now)
        if self.replay_idx >= len(self.replay_rows):
            self.replay_done = True

    def setup_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", self.port))
            sock.setblocking(False)
            self.sock = sock
            self.sock_ok = True
        except OSError as exc:
            self.bind_error = str(exc)
            try:
                sock.close()
            except OSError:
                pass

    def setup_log(self):
        if self.no_log or self.self_test:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = self.log_dir / f"gui_{stamp}.csv"
        self.log_file = open(self.log_path, "w", newline="")
        self.log_file.write(core.CSV_HEADER)

    def on_resize(self, event):
        self.layout = self.compute_layout(event.width, event.height)
        self.draw_static()

    def compute_layout(self, w, h):
        avail_h = max(200.0, h - TOP - BOTTOM)
        avail_w = max(160.0, w / 2.0 - 110.0)
        scale = min(avail_h / H_OUT, avail_w / W_OUT)
        top = TOP + (avail_h - H_OUT * scale) / 2.0
        return {
            "w": w,
            "h": h,
            "scale": scale,
            "top": top,
            "centers": {"L": w * 0.25, "R": w * 0.75},
        }

    def to_canvas(self, x, y, foot):
        lay = self.layout
        direction = 1.0 if foot == "R" else -1.0
        cx = lay["centers"][foot] + (x - XMID) * lay["scale"] * direction
        cy = lay["top"] + (y - MINY) * lay["scale"]
        return cx, cy

    def node_point(self, nx, ny, foot):
        x = MINX + nx * W_OUT
        y = MINY + ny * H_OUT
        return self.to_canvas(x, y, foot)

    def draw_static(self):
        canvas = self.canvas
        canvas.delete("static")
        if self.layout is None:
            return
        for foot in ("L", "R"):
            coords = []
            for x, y in OUTLINE:
                cx, cy = self.to_canvas(x, y, foot)
                coords.extend((cx, cy))
            canvas.create_polygon(
                coords, fill=FOOT_FILL, outline=FOOT_STROKE, width=2, tags="static"
            )
            label = "LEFT FOOT  (id=L)" if foot == "L" else "RIGHT FOOT  (id=R)"
            canvas.create_text(
                self.layout["centers"][foot],
                TOP - 26,
                text=label,
                fill=TITLE,
                font=("Segoe UI", 11, "bold"),
                tags="static",
            )
        if self.bind_error:
            canvas.create_text(
                self.layout["w"] / 2.0,
                14,
                text=f"UDP {self.port} busy - close logger.py, then restart the GUI",
                fill="#ff6b6b",
                font=("Segoe UI", 10, "bold"),
                tags="static",
            )
        self.draw_legend()
        canvas.tag_lower("static")

    def draw_legend(self):
        lay = self.layout
        canvas = self.canvas
        bar_w, bar_h = 240.0, 12.0
        x0 = lay["w"] / 2.0 - bar_w / 2.0
        y0 = lay["h"] - 34.0
        steps = 60
        for i in range(steps):
            t = i / (steps - 1)
            x = x0 + bar_w * i / steps
            canvas.create_rectangle(
                x, y0, x + bar_w / steps + 1, y0 + bar_h,
                fill=hexc(pressure_rgb(t)), outline="", tags="static",
            )
        canvas.create_text(
            x0 - 10, y0 + bar_h / 2, text="0", anchor="e",
            fill=DIM, font=("Consolas", 9), tags="static",
        )
        canvas.create_text(
            x0 + bar_w + 10, y0 + bar_h / 2, text=f"{self.legend_max:.0f}", anchor="w",
            fill=DIM, font=("Consolas", 9), tags="static",
        )
        canvas.create_text(
            lay["w"] / 2.0, y0 - 12, text=self.legend_label,
            fill=DIM, font=("Segoe UI", 9), tags="static",
        )

    def receive(self):
        if not self.sock_ok:
            return
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except (BlockingIOError, OSError):
                break
            parsed = core.handle_frame(
                data.decode("ascii", "ignore"), addr, self.sock, True
            )
            if parsed:
                now, dev, seq, millis, vals = parsed
                if self.asym is not None:
                    self.asym.add(dev, vals, now, millis)
                if self.log_file is not None:
                    self.log_buf.append(core.format_row(now, dev, seq, millis, vals))

    def inject_test_data(self):
        t = time.time() - self.start
        base = {"L": [1800, 900, 2500, 600], "R": [2200, 1600, 700, 1200]}
        now = time.time()
        for dev in ("L", "R"):
            st = core.STATE[dev]
            for i in range(4):
                wave = 0.5 + 0.5 * math.sin(
                    t * 1.8 + i * 1.3 + (0.7 if dev == "R" else 0.0)
                )
                st["vals"][i] = int(min(proc.ADC_MAX, base[dev][i] * (0.25 + 1.4 * wave)))
            st["last_t"] = now
            st["times"].append(now)
            if self.asym is not None:
                self.asym.add(dev, st["vals"], now, int(t * 1000))

    def format_value(self, raw, force_n):
        if self.units == "raw":
            return f"{int(round(raw))}"
        if self.units == "N":
            return f"{force_n:.0f} N"
        if self.units == "pct":
            return f"{force_n / self.full_scale_n * 100.0:.0f}%"
        if self.units == "bw":
            if self.body_kg and self.body_kg > 0:
                return f"{force_n / (self.body_kg * 9.81) * 100.0:.0f}%BW"
            return f"{force_n / self.full_scale_n * 100.0:.0f}%"
        return f"{int(round(raw))}"

    def draw_nodes(self):
        if self.layout is None:
            return
        canvas = self.canvas
        canvas.delete("dyn")
        now = time.time()

        for foot in ("L", "R"):
            st = core.STATE[foot]
            fresh = bool(st["last_t"]) and (now - st["last_t"] < 2.0)
            for idx, (label, nx, ny, side) in enumerate(NODES):
                raw = st["vals"][idx] if (fresh and st["vals"][idx] is not None) else 0
                conv = proc.convert_sensor(raw, foot, idx, self.calib)
                force_n = conv["force_n"]
                shown = self.format_value(raw, force_n)
                cx, cy = self.node_point(nx, ny, foot)
                active = conv["raw_adj"] > ZERO_EPS

                norm = proc.clamp(
                    conv["raw_adj"] / self.color_full_raw, 0.0, 1.0
                )

                if active:
                    rgb = pressure_rgb(norm)
                    radius = 4.0 + 13.0 * (norm ** 0.55)
                    intensity = 0.45 + 0.55 * norm
                    for spread, alpha in ((3.1, 0.07), (2.1, 0.16), (1.45, 0.40), (1.0, None)):
                        rad = radius * spread
                        a = 1.0 if alpha is None else alpha * intensity
                        canvas.create_oval(
                            cx - rad, cy - rad, cx + rad, cy + rad,
                            fill=hexc(blend(rgb, a)), outline="", tags="dyn",
                        )
                    text_color = hexc(rgb)
                    font = ("Segoe UI", 10, "bold")
                    gap = radius + 8
                else:
                    text_color = DIM
                    font = ("Segoe UI", 9)
                    gap = 8

                if side == "auto":
                    direction = 1
                elif foot == "R":
                    direction = -1 if side == "in" else 1
                else:
                    direction = 1 if side == "in" else -1

                canvas.create_text(
                    cx + direction * gap, cy,
                    text=f"{label}: {shown}",
                    anchor="w" if direction > 0 else "e",
                    fill=text_color, font=font, tags="dyn",
                )

            if not fresh:
                canvas.create_text(
                    self.layout["centers"][foot],
                    self.layout["top"] + H_OUT * self.layout["scale"] + 18,
                    text="no data", fill=DIM,
                    font=("Segoe UI", 9), tags="dyn",
                )

    def flush_log(self, now):
        if self.log_file is None or now - self.last_flush < 0.5:
            return
        if self.log_buf:
            self.log_file.writelines(self.log_buf)
            self.log_buf.clear()
        self.log_file.flush()
        self.last_flush = now

    def update_stats(self):
        tag = "CAL" if proc.is_calibrated(self.calib) else "APPROX"
        units = self.units
        if units == "bw" and not (self.body_kg and self.body_kg > 0):
            units = "pct(no kg)"
        if self.replay_path:
            total = len(self.replay_rows)
            name = Path(self.replay_path).name
            state = "end" if self.replay_done else "replay"
            self.stats_var.set(
                f"{state} {name}  {self.replay_idx}/{total}  units={units}  {tag}"
            )
            return

        l_state, r_state = core.STATE["L"], core.STATE["R"]
        now = time.time()
        core.update_rate(l_state, now)
        core.update_rate(r_state, now)
        if self.bind_error:
            prefix = f"UDP {self.port} BUSY   "
        else:
            prefix = ""
        log_name = self.log_path.name if self.log_path else "logging off"
        self.stats_var.set(
            f"{prefix}L {l_state['rate']:4.1f}/s  R {r_state['rate']:4.1f}/s  "
            f"drops {l_state['drops']}/{r_state['drops']}  "
            f"units={units}  {tag}  {log_name}"
        )

    def update_asym_display(self):
        if self.asym is None:
            self.asym_var.set("ASYMMETRY: off")
            self.asym_detail_var.set("")
            return
        sat = self.asym.saturation_warning()
        ignored_txt = (
            " | ignored: " + ", ".join(self.asym.ignored_labels)
            if self.asym.ignored_labels else ""
        )
        if sum(self.asym.mask) < 3:
            parts = [
                f"{'left' if side == 'L' else 'right'} {label} {frac * 100:.0f}%"
                for side, items in sat.items() for label, frac in items
            ]
            self.asym_var.set("ASYMMETRY: sensor saturating - check insole")
            self.asym_label.configure(fg="#ff9f43")
            self.asym_detail_var.set("railed: " + ", ".join(parts))
            return
        report = self.asym.report
        if report is None or report.get("status") != "ok":
            n_l = report.get("n_L", 0) if report else 0
            n_r = report.get("n_R", 0) if report else 0
            self.asym_var.set(
                f"ASYMMETRY: collecting  L {n_l} / R {n_r} cycles"
            )
            self.asym_label.configure(fg=DIM)
            self.asym_detail_var.set(
                f"window {self.asym.window} cycles/foot{ignored_txt}"
            )
            return

        color = {
            "symmetric": "#4caf50",
            "borderline": "#9aa7b5",
            "mild": "#ffd166",
            "moderate": "#ff9f43",
            "severe": "#ff5c5c",
        }.get(report["severity"], DIM)
        flag = "ASYMMETRIC" if report["asymmetric"] else "not flagged"
        self.asym_var.set(
            f"ASYMMETRY: {report['severity'].upper()}  "
            f"score {report['score']:.0f}/100  ({flag})"
        )
        self.asym_label.configure(fg=color)

        drivers = ", ".join(report.get("driving_metrics") or []) or "-"
        swing_si = self.asym.latest_si("swing")
        swing_txt = f"{swing_si:+.0f}%" if swing_si is not None else "-"
        limited = report.get("sensor_limited") or []
        limited_txt = (
            f" | not scored (sensor-limited): {', '.join(limited)}"
            if limited else ""
        )
        self.asym_detail_var.set(
            f"side {report['side']} | drivers {drivers} | "
            f"window L/R {report['n_L']}/{report['n_R']} | swing SI {swing_txt}"
            f"{ignored_txt}{limited_txt}"
        )
        if report.get("id") != self.last_asym_id:
            self.last_asym_id = report["id"]
            self._log_asym(report)

    def _log_asym(self, report):
        if self.replay_path or self.self_test or self.no_log:
            return
        if self.asym_log_file is None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.asym_log_path = self.log_dir / f"asym_{stamp}.csv"
            self.asym_log_file = open(self.asym_log_path, "w", newline="")
            self.asym_log_file.write(
                "host_time,score,severity,asymmetric,side,drivers,"
                + ",".join(f"{name}_si" for name in SI_FIELDS) + "\n"
            )

        metrics = report.get("metrics", {})

        def metric_si(name):
            metric = metrics.get(name)
            return f"{metric['si']:.2f}" if metric and metric.get("si") is not None else ""

        self.asym_log_file.write(
            f"{time.time():.3f},{report['score']:.2f},{report['severity']},"
            f"{int(report['asymmetric'])},{report['side']},"
            f"{'|'.join(report.get('driving_metrics') or [])},"
            + ",".join(metric_si(name) for name in SI_FIELDS) + "\n"
        )
        self.asym_log_file.flush()

    def tick(self):
        now = time.time()
        if self.self_test:
            self.inject_test_data()
        elif self.replay_path:
            self.advance_replay(now)
        else:
            self.receive()
        self.flush_log(now)
        self.draw_nodes()
        self.update_stats()
        self.update_asym_display()
        self.root.after(40, self.tick)

    def on_close(self):
        try:
            if self.log_file is not None:
                if self.log_buf:
                    self.log_file.writelines(self.log_buf)
                    self.log_buf.clear()
                self.log_file.flush()
                self.log_file.close()
                core.write_snapshot(self.log_dir)
            if self.asym_log_file is not None:
                self.asym_log_file.close()
                self.asym_log_file = None
            if self.sock is not None:
                self.sock.close()
        finally:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="FootGait live insole GUI")
    parser.add_argument("--port", type=int, default=4210)
    parser.add_argument("--no-log", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--demo", action="store_true", help="alias for --self-test")
    parser.add_argument("--replay", default=None, help="CSV log to replay")
    parser.add_argument("--replay-start", type=float, default=0.0,
                        help="seconds into the log to start")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed")
    parser.add_argument("--units", choices=["raw", "N", "pct", "bw"], default="N")
    parser.add_argument("--body-kg", type=float, default=None)
    parser.add_argument("--calibration", default=None, help="calibration.json path")
    parser.add_argument("--no-asym", action="store_true",
                        help="disable the live asymmetry badge")
    parser.add_argument("--asym-window", type=int, default=12,
                        help="cycles per foot in the asymmetry window")
    parser.add_argument("--asym-threshold", type=float, default=3.0,
                        help="minimum contact force for online event detection (N)")
    parser.add_argument("--color-full-raw", type=float, default=3000.0,
                        help="raw ADC value that maps to full red")
    args = parser.parse_args()

    self_test = args.self_test or args.demo
    if args.replay:
        self_test = False

    root = tk.Tk()
    app = FootGaitGUI(
        root,
        args.port,
        args.no_log,
        self_test=self_test,
        replay=args.replay,
        units=args.units,
        body_kg=args.body_kg,
        speed=args.speed,
        calibration=args.calibration,
        replay_start=args.replay_start,
        asym_enabled=not args.no_asym,
        asym_window=args.asym_window,
        asym_threshold=args.asym_threshold,
        color_full_raw=args.color_full_raw,
    )
    if self_test:
        root.after(1600, app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
