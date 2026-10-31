"""Synthetic gait generator for validating the asymmetry detector.

Generates a CSV in the same format as the live logger:

    host_time,id,seq,millis,fsr0,fsr1,fsr2,fsr3

The raw ADC values are produced by inverting the approximate force model in
processing.py, so analyze.py should recover the injected asymmetry.

Examples
--------
    python simulate.py --out logs/sim_symmetric.csv
    python simulate.py --out logs/sim_asym.csv --swing-asym 0.20 --peak-asym 0.20
    python simulate.py --out logs/sim_asym.csv --swing-asym 0.20 --check
"""

import argparse
import csv
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

import asymmetry as asym
import logger as core
import processing as proc

STRIDE_L = 1.10
STANCE_L = 0.68
SWING_L = STRIDE_L - STANCE_L
AMPS = np.array([32.0, 27.0, 27.0, 22.0])
PHASE = 0.50


def force_to_raw(force_n):
    if force_n <= 0.0:
        return 0
    g_us = min(proc.force_to_conductance(force_n), proc.G_MAX_US)
    r_fsr = 1e6 / g_us
    mv = proc.VCC_MV * proc.R_FIXED_OHM / (proc.R_FIXED_OHM + r_fsr)
    raw = mv / proc.VCC_MV * proc.ADC_MAX
    return int(round(min(proc.ADC_MAX, max(0.0, raw))))


def stance_forces(p, amps):
    if p >= 1.0:
        return np.zeros(proc.COUNT)
    shape = np.array([
        math.exp(-((p - 0.10) / 0.12) ** 2),
        math.exp(-((p - 0.38) / 0.18) ** 2),
        math.exp(-((p - 0.46) / 0.20) ** 2),
        math.exp(-((p - 0.70) / 0.16) ** 2),
    ])
    base = 0.10 * math.sin(math.pi * p)
    return amps * (shape + base)


def generate(seconds, swing_asym, peak_asym, noise, out_path):
    swing_r = SWING_L * (1.0 + swing_asym)
    stride_r = STANCE_L + swing_r
    stance_frac_l = STANCE_L / STRIDE_L
    stance_frac_r = STANCE_L / stride_r
    amps_r = AMPS * (1.0 + peak_asym)

    fs = 50.0
    n = int(seconds * fs)
    t0 = time.time()
    seq = {"L": 0, "R": 0}
    rng = np.random.default_rng(11)

    rows = []
    for i in range(n):
        t = i / fs
        phase_l = ((t - PHASE * STRIDE_L) % STRIDE_L) / STRIDE_L
        phase_r = ((t - PHASE * STRIDE_L - 0.5 * STRIDE_L) % stride_r) / stride_r

        f_l = stance_forces(
            phase_l / stance_frac_l if phase_l < stance_frac_l else 1.0, AMPS
        )
        f_r = stance_forces(
            phase_r / stance_frac_r if phase_r < stance_frac_r else 1.0, amps_r
        )

        for side, forces in (("L", f_l), ("R", f_r)):
            seq[side] += 1
            raw = [force_to_raw(f) for f in forces]
            raw = [int(np.clip(v + rng.normal(0.0, noise), 0, proc.ADC_MAX))
                   for v in raw]
            rows.append((
                f"{t0 + t:.3f}", side, seq[side], int(t * 1000),
                raw[0], raw[1], raw[2], raw[3],
            ))

    with open(out_path, "w", newline="") as handle:
        handle.write(core.CSV_HEADER)
        writer = csv.writer(handle)
        writer.writerows(rows)

    expected_swing_si = asym.si(SWING_L, swing_r)
    expected_peak_si = asym.si(1.0, 1.0 + peak_asym)
    expected_stride_si = asym.si(STRIDE_L, stride_r)
    print(f"wrote {out_path}  ({len(rows)} rows, {seconds:.0f}s, 50 Hz)")
    print(f"stride L/R      {STRIDE_L:.3f} / {stride_r:.3f} s")
    print(f"swing  L/R      {SWING_L:.3f} / {swing_r:.3f} s  "
          f"(expected SI {expected_swing_si:+.1f}%)")
    print(f"stride SI       {expected_stride_si:+.1f}%")
    print(f"peak   SI       {expected_peak_si:+.1f}%")
    print(f"stance SI       {asym.si(STANCE_L, STANCE_L):+.1f}%")
    return expected_swing_si, expected_peak_si


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic gait logs")
    parser.add_argument("--out", default="logs/sim_gait.csv")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--swing-asym", type=float, default=0.0,
                        help="fractional swing-time increase on R (e.g. 0.2)")
    parser.add_argument("--peak-asym", type=float, default=0.0,
                        help="fractional peak-force increase on R (e.g. 0.2)")
    parser.add_argument("--noise", type=float, default=6.0,
                        help="raw ADC noise std dev")
    parser.add_argument("--symmetric", action="store_true",
                        help="force symmetric (overrides asym arguments)")
    parser.add_argument("--check", action="store_true",
                        help="run analyze.py on the generated file")
    args = parser.parse_args()

    if args.symmetric:
        args.swing_asym = 0.0
        args.peak_asym = 0.0

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    generate(args.seconds, args.swing_asym, args.peak_asym, args.noise, out_path)

    if args.check:
        analyze = Path(__file__).resolve().parent / "analyze.py"
        out_dir = out_path.parent / f"analysis_{out_path.stem}"
        cmd = [sys.executable, str(analyze), "--file", str(out_path),
               "--out", str(out_dir)]
        print("\n--- running analysis ---")
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
