"""Offline gait and asymmetry analysis for FootGait CSV logs.

Converts raw FSR readings to approximate/calibrated force, finds stance
intervals, derives per-cycle features (features.py, shared with the live
badge), and reports bilateral asymmetry.

    python analyze.py --file logs/gui_20261019_175610.csv
    python analyze.py --file logs/gui_20261019_175610.csv --device R
    python analyze.py --file logs/gui_20261019_175610.csv --calibration calibration.json

Outputs (in analysis/ by default):
    asymmetry_report.json   metric table + composite score + classification
    cycles.csv              one row per detected stance/stride
    total_force.png         L/R total force with stance shading
    sensors_L/R.png         per-sensor force
    cop_L/R.png             CoP trajectory per foot
    cop_overlay.png         L/R CoP overlaid
    asymmetry.png           |SI| / threshold per metric
    metric_compare.png      L/R boxplots per metric
    cycle_series.png        per-cycle stance/swing/peak
    symmetry.png            simple L/R bar comparison
"""

import argparse
import csv
import json
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import asymmetry as asym
import features
import processing as proc
import sensor_config as sc

OUT_DIR = Path(__file__).resolve().parent / "analysis"
POS = np.asarray(sc.POS_XY, dtype=float)


def load_log(path):
    data = {side: {"t": [], "millis": [], "raw": []} for side in proc.SIDES}
    for t, millis, side, vals in proc.read_rows(path, as_float=True):
        data[side]["t"].append(t)
        data[side]["millis"].append(millis)
        data[side]["raw"].append(vals)
    for side in data:
        if data[side]["t"]:
            order = np.argsort(np.asarray(data[side]["t"]))
            data[side]["t"] = np.asarray(data[side]["t"])[order]
            data[side]["millis"] = np.asarray(data[side]["millis"])[order]
            data[side]["raw"] = np.asarray(data[side]["raw"])[order]
    return data


def rolling_median(x, k=5):
    if k <= 1 or len(x) < k:
        return x.copy()
    pad = k // 2
    padded = np.pad(x, pad, mode="edge")
    out = np.empty_like(x)
    for i in range(len(x)):
        out[i] = np.median(padded[i:i + k])
    return out


def detect_contacts(total, dt):
    if len(total) < 5:
        return [], 0.0
    window = max(1, int(round(0.10 / max(dt, 1e-3))))
    kernel = np.ones(window) / window
    smooth = np.convolve(total, kernel, mode="same")
    baseline = float(np.percentile(smooth, 20))
    peak = float(np.percentile(smooth, 95))
    span = max(peak - baseline, 1e-6)
    thr = max(3.0, baseline + 0.30 * span)
    off_thr = baseline + 0.10 * span
    on = False
    start = 0
    intervals = []
    for i, value in enumerate(smooth):
        if not on and value > thr:
            on = True
            start = i
        elif on and value < off_thr:
            on = False
            intervals.append((start, i))
    if on:
        intervals.append((start, len(smooth) - 1))
    min_len = max(2, int(round(0.25 / max(dt, 1e-3))))
    intervals = [iv for iv in intervals if iv[1] - iv[0] >= min_len]
    return intervals, thr


def side_values(results, key):
    out = {"L": [], "R": []}
    for res in results:
        out[res["side"]] = [c[key] for c in res["cycles"] if c.get(key) is not None]
    return out["L"], out["R"]


def invalid_channels(data, rail_fraction=0.05):
    """Channels railed at the ADC limit often enough to be untrustworthy."""
    invalid = set()
    for side in data:
        raw = np.asarray(data[side]["raw"], dtype=float)
        if len(raw) < 50:
            continue
        for i in range(proc.COUNT):
            if np.mean(raw[:, i] >= proc.ADC_MAX - 2) >= rail_fraction:
                invalid.add(i)
    if len(invalid) >= proc.COUNT - 1:
        return []
    return sorted(invalid)


def analyze_device(side, millis, host, raw, cal, mask=None):
    millis = np.asarray(millis, dtype=float)
    host = np.asarray(host, dtype=float)
    raw = np.asarray(raw, dtype=float)
    n_all = len(millis)
    if n_all < 10:
        return None

    cuts = [0]
    for i in range(1, n_all):
        gap_ms = millis[i] - millis[i - 1]
        gap_host = host[i] - host[i - 1]
        if millis[i] <= millis[i - 1] or gap_ms > 2000 or gap_host > 2.0:
            cuts.append(i)
    cuts.append(n_all)
    runs = [(cuts[k], cuts[k + 1]) for k in range(len(cuts) - 1)]
    start, end = max(runs, key=lambda r: r[1] - r[0])
    t = (millis[start:end] - millis[start]) / 1000.0
    host_seg = host[start:end]
    raw = raw[start:end]

    n = len(t)
    if n < 10:
        return None
    dt = float(np.median(np.diff(t))) if n > 1 else 0.02

    forces = np.zeros((n, proc.COUNT))
    clipped_frames = np.zeros(n, dtype=bool)
    index = np.arange(n)
    for j in range(proc.COUNT):
        raw_j = raw[:, j]
        valid = raw_j < proc.ADC_MAX - 2
        clipped_frames |= ~valid
        col = np.array([
            proc.convert_sensor(raw_j[i], side, j, cal)["force_n"] for i in range(n)
        ])
        if valid.any() and not valid.all():
            col = np.interp(index, index[valid], col[valid])
        elif not valid.any():
            col = np.zeros(n)
        forces[:, j] = rolling_median(col, 5)
    if mask is not None:
        for j, valid_channel in enumerate(mask):
            if not valid_channel:
                forces[:, j] = 0.0
    total = forces.sum(axis=1)

    intervals, thr = detect_contacts(total, dt)
    starts = [iv[0] for iv in intervals]

    cop_x = np.full(n, np.nan)
    cop_y = np.full(n, np.nan)
    for i in range(n):
        w = np.clip(forces[i], 0.0, None)
        s = w.sum()
        if s > 1e-6:
            cop_x[i] = float((w * POS[:, 0]).sum() / s)
            cop_y[i] = float((w * POS[:, 1]).sum() / s)

    cycles = []
    for ci, (s, e) in enumerate(intervals):
        if e - 1 <= s:
            continue
        start_t = t[s]
        end_t = t[e - 1]
        stance = end_t - start_t
        if stance < 0.15 or stance > 3.0:
            continue
        prev_start = t[starts[ci - 1]] if ci > 0 else None
        feats = features.cycle_features(
            t[s:e], forces[s:e], total[s:e], cop_x[s:e], cop_y[s:e],
            start_t, end_t, prev_start,
        )
        if feats is None:
            continue
        if mask is not None and not mask[0]:
            feats["heel_share"] = None
        feats["index"] = ci
        feats["cop_x"] = cop_x[s:e]
        feats["cop_y"] = cop_y[s:e]
        cycles.append(feats)

    def values(key):
        return [c[key] for c in cycles if c.get(key) is not None]

    stance_vals = values("stance")
    swing_vals = values("swing")
    stride_vals = values("stride")
    peaks = values("peak")
    impulses = values("impulse")
    rates = values("loading_rate")

    stance_mean = float(np.median(stance_vals)) if stance_vals else 0.0
    swing_mean = float(np.median(swing_vals)) if swing_vals else 0.0
    stride_mean = float(np.median(stride_vals)) if stride_vals else 0.0
    cadence = (120.0 / stride_mean) if stride_mean > 0 else 0.0

    return {
        "side": side,
        "runs": len(runs),
        "clipped": int(clipped_frames.sum()),
        "t": t,
        "host": host_seg,
        "raw": raw,
        "forces": forces,
        "total": total,
        "dt": dt,
        "intervals": intervals,
        "threshold": thr,
        "cycles": cycles,
        "event_host": host_seg[starts] if starts else np.array([]),
        "stance_mean": stance_mean,
        "swing_mean": swing_mean,
        "stride_mean": stride_mean,
        "cadence": cadence,
        "peaks": peaks,
        "impulses": impulses,
        "rates": rates,
        "cop_x": cop_x,
        "cop_y": cop_y,
    }


def compute_step_metric(results):
    events = []
    for res in results:
        for event in res.get("event_host", []):
            events.append((float(event), res["side"]))
    events.sort()
    lr = []
    rl = []
    for (t1, s1), (t2, s2) in zip(events, events[1:]):
        if s1 == s2:
            continue
        dt = t2 - t1
        if 0.15 <= dt <= 2.5:
            (lr if s1 == "L" else rl).append(dt)
    if len(lr) < 2 or len(rl) < 2:
        return None
    return {
        "L": float(np.median(lr)),
        "R": float(np.median(rl)),
        "L_vals": lr,
        "R_vals": rl,
    }


def build_report(results, step, path, cal, ignored=None):
    if len(results) < 2:
        return {
            "file": str(path),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "calibrated": bool(proc.is_calibrated(cal)),
            "cycles": {res["side"]: len(res["cycles"]) for res in results},
            "metrics": {},
            "composite_score": 0.0,
            "asymmetric": False,
            "severity": "not comparable",
            "side": "n/a",
            "driving_metrics": [],
            "flagged_count": 0,
            "note": "single foot: no bilateral comparison",
            "caveats": ["only one foot analysed: symmetry is not measurable"],
        }

    metrics = {}
    for name, m in asym.METRIC_DEFS.items():
        if name == "step":
            if not step:
                continue
            l_vals, r_vals = step["L_vals"], step["R_vals"]
        else:
            l_vals, r_vals = side_values(results, name)
        if not l_vals or not r_vals:
            continue
        l_med = float(np.median(l_vals))
        r_med = float(np.median(r_vals))
        si_val = asym.si(l_med, r_med)
        ci = asym.bootstrap_ci(l_vals, r_vals)
        metrics[name] = {
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

    limited = (
        [name for name in ("peak", "impulse", "heel_share",
                           "cop_path", "cop_ml_range") if name in metrics]
        if ignored else []
    )
    scored = ({name: m for name, m in metrics.items()
               if name not in limited} if limited else metrics)
    score = asym.composite(scored, noise_floor=0.5 if limited else 0.0)
    classification = asym.classify(scored, score)
    caveats = [
        "forces are approximate until calibration.json is populated"
        if not proc.is_calibrated(cal) else "per-sensor calibration applied",
        "single trial; thresholds and severity bands are provisional",
        "no ground-truth labels: this is a descriptive asymmetry report",
        "4 FSRs/foot: CoP is approximate and there is no contact-area measure",
    ]
    clipped = sum(res["clipped"] for res in results)
    if clipped:
        caveats.append(f"{clipped} saturated frames were interpolated")
    if ignored:
        labels = ", ".join(sc.SENSOR_LABELS[i] for i in ignored)
        caveats.append(
            f"saturated channel(s) ignored on both feet: {labels}"
        )
        caveats.append(
            "absolute force metrics were not scored because a channel is missing"
        )

    report = {
        "file": str(path),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "calibrated": bool(proc.is_calibrated(cal)),
        "cycles": {res["side"]: len(res["cycles"]) for res in results},
        "metrics": metrics,
        "composite_score": score,
        **classification,
        "caveats": caveats,
    }
    if ignored:
        report["ignored_channels"] = [sc.SENSOR_LABELS[i] for i in ignored]
        report["sensor_limited"] = limited
    if "swing" in metrics:
        report["plotnik_swing_ga"] = asym.plotnik_ga(
            metrics["swing"]["L"], metrics["swing"]["R"]
        )
    return report


def print_summary(res):
    duration = res["t"][-1] - res["t"][0] if len(res["t"]) > 1 else 0.0
    print(f"\n[{res['side']}] {duration:.1f}s of data, "
          f"dt={res['dt'] * 1000:.1f}ms, cycles={len(res['cycles'])}, "
          f"thr={res['threshold']:.2f}N, runs={res['runs']}")
    if res["clipped"]:
        print(f"  clipped frames: {res['clipped']} (interpolated)")
    print(f"  stance  {res['stance_mean'] * 1000:7.0f} ms")
    print(f"  swing   {res['swing_mean'] * 1000:7.0f} ms")
    print(f"  stride  {res['stride_mean'] * 1000:7.0f} ms")
    print(f"  cadence {res['cadence']:7.1f} steps/min")
    if res["peaks"]:
        print(f"  peak    {np.median(res['peaks']):7.1f} N")
        print(f"  impulse {np.median(res['impulses']):7.2f} N*s")


def print_asymmetry(report):
    print("\n=== Gait asymmetry ===")
    print(f"cycles L/R: {report['cycles'].get('L', 0)}/{report['cycles'].get('R', 0)}")
    if report.get("note"):
        print(f"note: {report['note']}")
        return
    print(f"{'metric':14s} {'L':>9s} {'R':>9s} {'SI%':>8s} "
          f"{'CI95':>18s} {'flag':>5s}")
    for name, m in sorted(report["metrics"].items(),
                          key=lambda kv: abs(kv[1]["si"] or 0), reverse=True):
        ci = m["ci"]
        ci_txt = f"[{ci[0]:+.1f}, {ci[1]:+.1f}]" if ci[0] is not None else "-"
        print(f"{name:14s} {m['L']:9.3f} {m['R']:9.3f} {m['si']:+8.1f} "
              f"{ci_txt:>18s} {'YES' if m['flag'] else '':>5s}")
    print(f"\ncomposite score : {report['composite_score']:.1f} / 100")
    print(f"severity        : {report['severity']}")
    print(f"direction       : {report['side']}")
    print(f"asymmetric      : {'YES' if report['asymmetric'] else 'no'} "
          f"({report['flagged_count']} flagged metrics; "
          f"needs 2+ flags and score >= 45)")
    if report["driving_metrics"]:
        print(f"driving metrics : {', '.join(report['driving_metrics'])}")
    if report.get("sensor_limited"):
        print("not scored      : " + ", ".join(report["sensor_limited"])
              + " (saturated channel ignored)")
    if "plotnik_swing_ga" in report and report["plotnik_swing_ga"] is not None:
        print(f"Plotnik swing GA: {report['plotnik_swing_ga']:.1f}%")
    print("caveats         : " + "; ".join(report["caveats"]))


def save(fig, out_dir, name):
    fig.tight_layout()
    fig.savefig(out_dir / name, dpi=130)
    plt.close(fig)


def plot_total(results, out_dir):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for res in results:
        ax.plot(res["t"], res["total"], label=res["side"], linewidth=1.2)
        for s, e in res["intervals"]:
            ax.axvspan(res["t"][s], res["t"][e - 1], alpha=0.06)
    ax.set_title("Total force per foot")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("force (N, approximate)")
    ax.legend()
    ax.grid(alpha=0.3)
    save(fig, out_dir, "total_force.png")


def plot_sensors(results, out_dir):
    for res in results:
        fig, ax = plt.subplots(figsize=(11, 4.5))
        for j, label in enumerate(sc.SENSOR_LABELS):
            ax.plot(res["t"], res["forces"][:, j], label=label, linewidth=1.0)
        ax.set_title(f"{res['side']} foot - per-sensor force")
        ax.set_xlabel("time (s)")
        ax.set_ylabel("force (N, approximate)")
        ax.legend(ncol=4)
        ax.grid(alpha=0.3)
        save(fig, out_dir, f"sensors_{res['side']}.png")


def plot_cop(results, out_dir):
    for res in results:
        fig, ax = plt.subplots(figsize=(5.5, 7))
        mask = np.isfinite(res["cop_x"])
        scatter = ax.scatter(
            res["cop_x"][mask], res["cop_y"][mask], c=res["t"][mask],
            s=8, cmap="viridis", alpha=0.8,
        )
        fig.colorbar(scatter, ax=ax, label="time (s)")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xlabel("medial  ->  lateral")
        ax.set_ylabel("heel  ->  toes")
        ax.set_title(f"{res['side']} foot - centre of pressure")
        ax.grid(alpha=0.3)
        save(fig, out_dir, f"cop_{res['side']}.png")


def plot_cop_overlay(results, out_dir):
    fig, ax = plt.subplots(figsize=(6, 7))
    colors = {"L": "#4c9be8", "R": "#e8734c"}
    for res in results:
        for cycle in res["cycles"]:
            cx = np.array(cycle["cop_x"], dtype=float)
            cy = np.array(cycle["cop_y"], dtype=float)
            if np.isfinite(cx).sum() >= 2:
                dist = np.hypot(np.diff(cx), np.diff(cy))
                for k in np.where(dist > 0.15)[0]:
                    cx[k + 1] = np.nan
                    cy[k + 1] = np.nan
                ax.plot(cx, cy, color=colors[res["side"]],
                        alpha=0.18, linewidth=1.0)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("medial  ->  lateral")
    ax.set_ylabel("heel  ->  toes")
    ax.set_title("CoP trajectories by cycle")
    ax.grid(alpha=0.3)
    save(fig, out_dir, "cop_overlay.png")


def plot_asymmetry(report, out_dir):
    names = [n for n, m in report["metrics"].items() if m["si"] is not None]
    if not names:
        return
    names.sort(key=lambda n: abs(report["metrics"][n]["si"]), reverse=True)
    values = [abs(report["metrics"][n]["si"]) / report["metrics"][n]["threshold"]
              for n in names]
    colors = ["#e85d4c" if report["metrics"][n]["flag"] else "#4c9be8"
              for n in names]

    fig, ax = plt.subplots(figsize=(8, 0.55 * len(names) + 2))
    y = np.arange(len(names))
    ax.barh(y, values, color=colors, alpha=0.85)
    ax.axvline(1.0, color="#888", linestyle="--", linewidth=1,
               label="threshold")
    ax.set_yticks(y)
    ax.set_yticklabels([f"{n}  ({report['metrics'][n]['si']:+.0f}%)"
                        for n in names])
    ax.invert_yaxis()
    ax.set_xlabel("|SI| / threshold")
    ax.set_title(f"Asymmetry score {report['composite_score']:.0f}/100 "
                 f"({report['severity']})")
    ax.legend(loc="lower right")
    ax.grid(axis="x", alpha=0.3)
    save(fig, out_dir, "asymmetry.png")


def plot_metric_compare(results, out_dir):
    keys = ["stance", "swing", "stride", "peak", "impulse", "cop_path"]
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.5))
    for ax, key in zip(axes.ravel(), keys):
        l_vals, r_vals = side_values(results, key)
        if l_vals and r_vals:
            ax.boxplot([l_vals, r_vals])
            ax.set_xticks([1, 2])
            ax.set_xticklabels(["L", "R"])
            ax.set_title(key)
            ax.grid(axis="y", alpha=0.3)
        else:
            ax.set_visible(False)
    fig.suptitle("Left / right per-cycle distributions")
    save(fig, out_dir, "metric_compare.png")


def plot_cycle_series(results, out_dir):
    keys = ["stance", "swing", "peak"]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    for ax, key in zip(axes, keys):
        for res in results:
            vals = [c[key] for c in res["cycles"] if c.get(key) is not None]
            if vals:
                ax.plot(range(1, len(vals) + 1), vals, marker="o",
                        markersize=3, linewidth=1, label=res["side"])
        ax.set_title(key)
        ax.set_xlabel("cycle")
        ax.grid(alpha=0.3)
        ax.legend()
    fig.suptitle("Per-cycle features")
    save(fig, out_dir, "cycle_series.png")


def plot_symmetry(results, out_dir):
    if len(results) < 2:
        return
    by_side = {res["side"]: res for res in results}
    left, right = by_side.get("L"), by_side.get("R")
    if left is None or right is None:
        return
    if not left["peaks"] or not right["peaks"]:
        return
    metrics = {
        "stance (ms)": (left["stance_mean"] * 1000, right["stance_mean"] * 1000),
        "peak (N)": (float(np.median(left["peaks"])),
                     float(np.median(right["peaks"]))),
        "impulse (N*s)": (float(np.median(left["impulses"])),
                          float(np.median(right["impulses"]))),
    }
    fig, axes = plt.subplots(1, len(metrics), figsize=(11, 3.6))
    for ax, (name, (lv, rv)) in zip(axes, metrics.items()):
        ax.bar(["L", "R"], [lv, rv], color=["#4c9be8", "#e8734c"])
        ax.set_title(name)
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle("Left / right comparison")
    save(fig, out_dir, "symmetry.png")


def write_cycles_csv(results, out_dir):
    fields = [
        "side", "cycle", "stance", "swing", "stride", "peak", "impulse",
        "loading_rate", "heel_share", "forefoot_share", "cop_path",
        "cop_ml_range", "cop_ap_range", "mean_cop_x", "mean_cop_y",
    ]
    with open(out_dir / "cycles.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for res in results:
            for cycle in res["cycles"]:
                row = {"side": res["side"]}
                for key in fields[1:]:
                    value = cycle.get(key)
                    row[key] = round(value, 4) if isinstance(value, float) else value
                writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description="FootGait offline analysis")
    parser.add_argument("--file", required=True)
    parser.add_argument("--device", choices=["L", "R", "all"], default="all")
    parser.add_argument("--calibration", default=None)
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"log not found: {path}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    cal = proc.load_calibration(args.calibration)
    data = load_log(path)
    ignored = invalid_channels(data) if args.device == "all" else []
    mask = [i not in ignored for i in range(proc.COUNT)] if ignored else None
    if ignored:
        print("ignored saturated channel(s) on both feet: "
              + ", ".join(sc.SENSOR_LABELS[i] for i in ignored))
    results = []
    for side in proc.SIDES:
        if args.device != "all" and side != args.device:
            continue
        if len(data[side]["t"]) == 0:
            continue
        res = analyze_device(
            side, data[side]["millis"], data[side]["t"], data[side]["raw"], cal,
            mask=mask,
        )
        if res is not None:
            results.append(res)
            print_summary(res)

    if not results:
        raise SystemExit("no usable rows found")

    step = compute_step_metric(results) if len(results) >= 2 else None
    report = build_report(results, step, path, cal, ignored=ignored)
    print_asymmetry(report)

    with open(out_dir / "asymmetry_report.json", "w") as handle:
        json.dump(report, handle, indent=2)
    write_cycles_csv(results, out_dir)

    plot_total(results, out_dir)
    plot_sensors(results, out_dir)
    plot_cop(results, out_dir)
    plot_cop_overlay(results, out_dir)
    plot_metric_compare(results, out_dir)
    plot_cycle_series(results, out_dir)
    plot_asymmetry(report, out_dir)
    plot_symmetry(results, out_dir)
    print(f"\nreport   {out_dir / 'asymmetry_report.json'}")
    print(f"plots    {out_dir}")


if __name__ == "__main__":
    main()
