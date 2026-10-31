"""Per-sensor calibration helper for FootGait.

Examples
--------
Estimate the no-load zero from a recording:

    python calibrate.py zero --from-log logs/gui_20261019_175610.csv --device R

Add a known-weight point (raw ADC, force in newtons):

    python calibrate.py add --device R --sensor 0 --raw 3100 --force 20

Show / reset:

    python calibrate.py show
    python calibrate.py reset --device R --sensor 0

Calibration is stored in calibration.json next to this file.
"""

import argparse
from pathlib import Path

import processing as p


def sides_for(device):
    return [side for side in p.SIDES if device in ("all", side)]


def save(cal, path):
    p.save_calibration(cal, path)
    print(f"saved {path or p.CALIBRATION_PATH}")


def refit(cal, side, idx):
    sc = p.sensor_cal(cal, side, idx)
    raw_points = sc.get("raw_points") or []
    if not raw_points:
        return None
    g_points = [
        [p.raw_to_conductance(raw, side, idx, cal), float(force)]
        for raw, force in raw_points
    ]
    a, b = p.set_linear(cal, side, idx, g_points)
    sc["raw_points"] = [[float(raw), float(force)] for raw, force in raw_points]
    return a, b


def cmd_zero(args):
    cal = p.load_calibration(args.file)
    sides = sides_for(args.device)

    if args.values:
        values = [float(v) for v in args.values.split(",")]
        if len(values) != p.COUNT:
            raise SystemExit("--values needs 4 comma-separated raw numbers")
        for side in sides:
            for idx, value in enumerate(values):
                p.sensor_cal(cal, side, idx)["zero_raw"] = value
                refit(cal, side, idx)
            print(f"[{side}] zero_raw = " + ", ".join(f"{v:.0f}" for v in values))
        save(cal, args.file)
        return

    if not args.from_log:
        raise SystemExit("provide --from-log or --values")
    path = Path(args.from_log)
    if not path.exists():
        raise SystemExit(f"log not found: {path}")
    rows = {side: [] for side in sides}
    for _t, _millis, side, vals in p.read_rows(path, sides):
        rows[side].append(vals)
    for side in sides:
        if not rows[side]:
            print(f"[{side}] no rows found")
            continue
        zeros = p.fill_zero_from_rows(cal, side, rows[side], args.percentile)
        print(f"[{side}] zero_raw = " + ", ".join(f"{z:.0f}" for z in zeros))
        for idx in range(p.COUNT):
            refit(cal, side, idx)
    save(cal, args.file)


def cmd_add(args):
    cal = p.load_calibration(args.file)
    sc = p.sensor_cal(cal, args.device, args.sensor)
    sc.setdefault("raw_points", [])
    sc["raw_points"].append([float(args.raw), float(args.force)])
    result = refit(cal, args.device, args.sensor)
    save(cal, args.file)
    if result is None:
        print("point stored (no fit yet)")
    else:
        a, b = result
        print(f"[{args.device}/{args.sensor}] force_N = {a:.6g} * G_uS + {b:.6g}")


def cmd_show(args):
    cal = p.load_calibration(args.file)
    print(f"file: {args.file or p.CALIBRATION_PATH}")
    print(f"vcc_mv={cal['vcc_mv']:.0f}  r_fixed={cal['r_fixed_ohm']:.0f}  "
          f"full_scale_N={cal['full_scale_n']:.0f}  calibrated={p.is_calibrated(cal)}")
    for side in p.SIDES:
        print(f"[{side}]")
        for idx in range(p.COUNT):
            sc = p.sensor_cal(cal, side, idx)
            mode = sc.get("mode", "approx")
            zero = sc.get("zero_raw", 0.0)
            a = sc.get("a")
            b = sc.get("b", 0.0)
            if mode == "linear" and a is not None:
                print(f"  {idx}: mode=linear  zero={zero:.0f}  "
                      f"F={a:.6g}*G+{b:.6g}  points={len(sc.get('raw_points', []))}")
            else:
                print(f"  {idx}: mode=approx  zero={zero:.0f}")


def cmd_reset(args):
    cal = p.load_calibration(args.file)
    sides = p.SIDES if args.device == "all" else (args.device,)
    indices = range(p.COUNT) if args.sensor is None else (args.sensor,)
    for side in sides:
        for idx in indices:
            cal["sensors"][side][str(idx)] = p.default_sensor_cal()
    save(cal, args.file)
    print(f"reset {args.device}/{args.sensor if args.sensor is not None else 'all'}")


def main():
    parser = argparse.ArgumentParser(description="FootGait calibration")
    parser.add_argument("--file", default=None, help="calibration JSON path")
    sub = parser.add_subparsers(dest="command", required=True)

    zero = sub.add_parser("zero", help="set zero from a log or explicit raw values")
    zero.add_argument("--from-log", default=None)
    zero.add_argument("--values", default=None,
                      help="comma-separated raw zero for fsr0..3")
    zero.add_argument("--device", choices=["L", "R", "all"], default="all")
    zero.add_argument("--percentile", type=float, default=10.0)
    zero.set_defaults(func=cmd_zero)

    add = sub.add_parser("add", help="add a known force point")
    add.add_argument("--device", choices=["L", "R"], required=True)
    add.add_argument("--sensor", type=int, choices=range(p.COUNT), required=True)
    add.add_argument("--raw", type=float, required=True)
    add.add_argument("--force", type=float, required=True)
    add.set_defaults(func=cmd_add)

    show = sub.add_parser("show", help="print calibration")
    show.set_defaults(func=cmd_show)

    reset = sub.add_parser("reset", help="reset sensors to approximate mode")
    reset.add_argument("--device", choices=["L", "R", "all"], default="all")
    reset.add_argument("--sensor", type=int, choices=range(p.COUNT), default=None)
    reset.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
