"""Online (rolling-window) gait asymmetry detector.

Feeds the same per-cycle features (features.py, shared with analyze.py) into a
small rolling window, so the GUI can show a live asymmetry badge. Intended for
live UDP, replay, and demos; the offline report remains the authoritative
analysis.

Timing uses the board's own `millis` clock when supplied (raw UDP/replay). Host
arrival time is bursty and must not drive stance/swing/stride/impulse. Host time
is still used for the inter-foot step metric, which needs a shared clock.
"""

from collections import deque

import numpy as np

import asymmetry as asym
import features
import processing as proc
import sensor_config as sc

POS = np.asarray(sc.POS_XY, dtype=float)

RAIL_FRACTION = 0.10      # rail this often -> channel is not trustworthy
MASK_MIN_SAMPLES = 250    # per foot, before deciding the channel mask
SENSOR_LIMITED = (
    "peak", "impulse", "heel_share", "cop_path", "cop_ml_range",
)


class FootState:
    def __init__(self, side, window):
        self.side = side
        self.samples = deque(maxlen=4000)
        self.recent = deque(maxlen=256)
        self.cycles = deque(maxlen=window)
        self.in_stance = False
        self.start_t = None
        self.prev_start = None
        self.last_t = None
        self.ms_base = None
        self.last_ms = None
        self.sat_window = deque(maxlen=500)


class OnlineAsymmetry:
    def __init__(self, calibration, window=12, min_cycles=5, threshold_n=3.0):
        self.calib = calibration
        self.window = window
        self.min_cycles = min_cycles
        self.threshold_n = threshold_n
        self.feet = {side: FootState(side, window) for side in proc.SIDES}
        self.events = deque(maxlen=64)
        self.report = None
        self._report_id = 0
        self.mask = [True] * proc.COUNT
        self.ignored_labels = []
        self.invalid = set()

    def add(self, side, raw_vals, t, millis=None):
        if side not in self.feet:
            return
        ft = self.feet[side]
        t_host = float(t)
        if millis is None:
            t_dev = t_host
        else:
            millis = int(millis)
            if ft.ms_base is None:
                ft.ms_base = millis
            elif millis < ft.last_ms or millis - ft.last_ms > 5000:
                # Board restarted or the stream gap is large; keep time
                # continuous instead of jumping backwards.
                ft.ms_base = millis - int((ft.last_t or 0.0) * 1000)
            t_dev = (millis - ft.ms_base) / 1000.0
            ft.last_ms = millis
        if ft.last_t is not None and t_dev <= ft.last_t:
            t_dev = ft.last_t + 0.001
        ft.last_t = t_dev

        forces = np.array([
            proc.convert_sensor(raw_vals[i], side, i, self.calib)["force_n"]
            for i in range(proc.COUNT)
        ])
        if not all(self.mask):
            forces = np.array([
                force if self.mask[i] else 0.0
                for i, force in enumerate(forces)
            ])
        total = float(forces.sum())
        ft.sat_window.append([raw >= proc.ADC_MAX - 2 for raw in raw_vals])
        self._refresh_mask()
        w = np.clip(forces, 0.0, None)
        s = float(w.sum())
        if s > 1e-6:
            cop_x = float((w * POS[:, 0]).sum() / s)
            cop_y = float((w * POS[:, 1]).sum() / s)
        else:
            cop_x = cop_y = np.nan

        ft.samples.append((t_dev, forces, total, cop_x, cop_y))
        ft.recent.append(total)

        if len(ft.recent) < 25:
            return

        arr = np.asarray(ft.recent, dtype=float)
        baseline = float(np.percentile(arr, 20))
        peak = float(np.percentile(arr, 95))
        span = max(peak - baseline, 1e-6)
        thr = max(self.threshold_n, baseline + 0.30 * span)
        off_thr = baseline + 0.10 * span

        if not ft.in_stance:
            if total > thr:
                ft.in_stance = True
                ft.start_t = t_dev
                self.events.append((t_host, side))
        elif total < off_thr:
            ft.in_stance = False
            self._close_cycle(ft, ft.start_t, t_dev)
            ft.prev_start = ft.start_t
            ft.start_t = None
            self._update_report()

    def _close_cycle(self, ft, start_t, end_t):
        samples = [s for s in ft.samples if start_t <= s[0] <= end_t]
        if len(samples) < 3:
            return
        stance = end_t - start_t
        if stance < 0.15 or stance > 3.0:
            return
        t = np.array([s[0] for s in samples], dtype=float)
        forces = np.array([s[1] for s in samples], dtype=float)
        total = np.array([s[2] for s in samples], dtype=float)
        cx = np.array([s[3] for s in samples], dtype=float)
        cy = np.array([s[4] for s in samples], dtype=float)

        feats = features.cycle_features(
            t, forces, total, cx, cy, start_t, end_t, ft.prev_start
        )
        if feats is None:
            return
        if not self.mask[0]:
            feats["heel_share"] = None
            feats["forefoot_share"] = None
        feats["index"] = len(ft.cycles)
        ft.cycles.append(feats)

    def _refresh_mask(self):
        """Ignore a channel on both feet once it rails too often."""
        if not all(len(ft.sat_window) >= MASK_MIN_SAMPLES
                   for ft in self.feet.values()):
            return
        invalid = set()
        for ft in self.feet.values():
            frac = np.asarray(ft.sat_window, dtype=float).mean(axis=0)
            invalid.update(i for i in range(proc.COUNT)
                           if frac[i] >= RAIL_FRACTION)
        self.invalid.update(invalid)
        invalid = self.invalid
        if len(invalid) >= proc.COUNT - 1:
            return
        mask = [i not in invalid for i in range(proc.COUNT)]
        if mask == self.mask:
            return
        self.mask = mask
        self.ignored_labels = [
            sc.SENSOR_LABELS[i] for i in range(proc.COUNT) if not mask[i]
        ]
        for ft in self.feet.values():
            ft.samples.clear()
            ft.recent.clear()
            ft.cycles.clear()
            ft.in_stance = False
            ft.start_t = None
            ft.prev_start = None
            ft.last_t = None
        self.events.clear()
        self.report = None

    def _metric(self, name, l_vals, r_vals):
        m = asym.METRIC_DEFS[name]
        l_med = float(np.median(l_vals))
        r_med = float(np.median(r_vals))
        si_val = asym.si(l_med, r_med)
        ci = asym.bootstrap_ci(l_vals, r_vals, n=400)
        return {
            "group": m.group,
            "threshold": m.threshold,
            "weight": m.weight,
            "unit": m.unit,
            "L": l_med,
            "R": r_med,
            "si": si_val,
            "ci": list(ci) if ci[0] is not None else [None, None],
            "flag": asym.flag_metric(si_val, ci, m.threshold),
            "n_L": len(l_vals),
            "n_R": len(r_vals),
        }

    def _update_report(self):
        counts = {side: len(self.feet[side].cycles) for side in proc.SIDES}
        if any(n < self.min_cycles for n in counts.values()):
            self.report = {
                "status": "collecting",
                "n_L": counts["L"],
                "n_R": counts["R"],
                "window": self.window,
                "id": self._report_id,
            }
            return

        metrics = {}
        for name in asym.METRIC_DEFS:
            if name == "step":
                continue
            l_vals = [c[name] for c in self.feet["L"].cycles
                      if c.get(name) is not None]
            r_vals = [c[name] for c in self.feet["R"].cycles
                      if c.get(name) is not None]
            if l_vals and r_vals:
                metrics[name] = self._metric(name, l_vals, r_vals)

        events = list(self.events)
        lr = []
        rl = []
        for (t1, s1), (t2, s2) in zip(events, events[1:]):
            if s1 == s2:
                continue
            dt = t2 - t1
            if 0.15 <= dt <= 2.5:
                (lr if s1 == "L" else rl).append(dt)
        if len(lr) >= 2 and len(rl) >= 2:
            metrics["step"] = self._metric("step", lr, rl)

        limited = (
            [name for name in SENSOR_LIMITED if name in metrics]
            if self.invalid else []
        )
        scored = ({name: m for name, m in metrics.items()
                   if name not in limited} if limited else metrics)
        score = asym.composite(scored, noise_floor=0.5 if limited else 0.0)
        classification = asym.classify(scored, score)
        self._report_id += 1
        self.report = {
            "status": "ok",
            "window": self.window,
            "n_L": counts["L"],
            "n_R": counts["R"],
            "metrics": metrics,
            "score": score,
            "id": self._report_id,
            "ignored_channels": list(self.ignored_labels),
            "sensor_limited": limited,
            **classification,
        }

    def saturation_warning(self, min_fraction=0.10):
        """Channels that are railed often enough to invalidate the score."""
        out = {}
        for side, ft in self.feet.items():
            if not ft.sat_window:
                continue
            frac = np.asarray(ft.sat_window, dtype=float).mean(axis=0)
            bad = [
                (sc.SENSOR_LABELS[i], float(frac[i]))
                for i in range(proc.COUNT) if frac[i] >= min_fraction
            ]
            if bad:
                out[side] = bad
        return out

    def latest_si(self, name):
        if not self.report or self.report.get("status") != "ok":
            return None
        metric = self.report["metrics"].get(name)
        return metric["si"] if metric else None
