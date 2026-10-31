"""Raw FSR reading -> usable force.

Pipeline
--------
    raw ADC  ->  millivolts  ->  FSR resistance  ->  conductance  ->  force

Wiring assumed (see hardware/wiring.md):

    3V3 ---[FSR402]---+--- GPIO (ADC)
                      |
                    [10k]
                      |
                     GND

    Vout = Vcc * R / (R + Rfsr)
    Rfsr = R * (Vcc - Vout) / Vout

Without calibration the force is the rough Adafruit/Interlink estimate.
With calibration.json the force uses a per-sensor linear fit on conductance.

Important: raw_to_mv() is a linear approximation. The ESP32 ADC is not
perfectly linear; sending analogReadMilliVolts() from the firmware later
will be more accurate.
"""

import csv
import json
import math
from pathlib import Path

VCC_MV = 3300.0
R_FIXED_OHM = 10000.0
ADC_MAX = 4095.0
FSR_FULL_SCALE_N = 100.0
G_MAX_US = 5000.0
FORCE_CAP_N = 150.0
G_KNEE_US = 1000.0
G_PER_N_LOW = 80.0
G_PER_N_HIGH = 30.0

SIDES = ("L", "R")
COUNT = 4
FSR_FIELDS = [f"fsr{i}" for i in range(COUNT)]

CALIBRATION_PATH = Path(__file__).resolve().parent / "calibration.json"


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def raw_to_mv(raw, vcc_mv=VCC_MV, adc_max=ADC_MAX):
    raw = clamp(float(raw), 0.0, adc_max)
    return raw / adc_max * vcc_mv


def mv_to_resistance(mv, vcc_mv=VCC_MV, r_fixed=R_FIXED_OHM):
    if mv <= 0.0:
        return math.inf
    if mv >= vcc_mv:
        return 0.0
    return r_fixed * (vcc_mv - mv) / mv


def resistance_to_conductance_us(r_ohm):
    if r_ohm <= 0.0:
        return math.inf
    if math.isinf(r_ohm):
        return 0.0
    return 1e6 / r_ohm


def approx_force_n(g_us):
    if g_us <= 0.0:
        return 0.0
    if g_us <= G_KNEE_US:
        return g_us / G_PER_N_LOW
    return (g_us - G_KNEE_US) / G_PER_N_HIGH


def force_to_conductance(force_n):
    if force_n <= 0.0:
        return 0.0
    if force_n <= G_KNEE_US / G_PER_N_LOW:
        return force_n * G_PER_N_LOW
    return G_KNEE_US + force_n * G_PER_N_HIGH


def default_sensor_cal():
    return {"zero_raw": 0.0, "mode": "approx", "a": None, "b": 0.0}


def default_calibration():
    return {
        "version": 1,
        "vcc_mv": VCC_MV,
        "r_fixed_ohm": R_FIXED_OHM,
        "full_scale_n": FSR_FULL_SCALE_N,
        "sensors": {
            side: {str(i): default_sensor_cal() for i in range(COUNT)}
            for side in SIDES
        },
    }


def load_calibration(path=None):
    path = Path(path) if path else CALIBRATION_PATH
    if not path.exists():
        return default_calibration()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return default_calibration()
        cal = default_calibration()
        cal["version"] = int(data.get("version", cal["version"]))
        cal["vcc_mv"] = float(data.get("vcc_mv", cal["vcc_mv"]))
        cal["r_fixed_ohm"] = float(data.get("r_fixed_ohm", cal["r_fixed_ohm"]))
        cal["full_scale_n"] = float(data.get("full_scale_n", cal["full_scale_n"]))

        sensors = data.get("sensors", {})
        if isinstance(sensors, dict):
            for side in SIDES:
                side_data = sensors.get(side, {})
                if not isinstance(side_data, dict):
                    continue
                for i in range(COUNT):
                    entry = side_data.get(str(i))
                    if isinstance(entry, dict) and entry:
                        merged = default_sensor_cal()
                        merged.update(entry)
                        cal["sensors"][side][str(i)] = merged
        return cal
    except (OSError, ValueError, TypeError):
        return default_calibration()


def save_calibration(cal, path=None):
    path = Path(path) if path else CALIBRATION_PATH
    path.write_text(json.dumps(cal, indent=2) + "\n")


def sensor_cal(cal, side, idx):
    return cal["sensors"][side][str(idx)]


def is_calibrated(cal):
    return any(
        sensor_cal(cal, side, i).get("mode") == "linear"
        for side in SIDES
        for i in range(COUNT)
    )


def fit_linear(points):
    pts = [(0.0, 0.0)] + [(float(x), float(y)) for x, y in points]
    n = len(pts)
    sx = sum(x for x, _ in pts)
    sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts)
    sxy = sum(x * y for x, y in pts)
    den = n * sxx - sx * sx
    if abs(den) < 1e-12:
        a = sxy / sxx if sxx > 1e-12 else 0.0
        b = 0.0
    else:
        a = (n * sxy - sx * sy) / den
        b = (sy - a * sx) / n
    return a, b


def set_linear(cal, side, idx, points):
    a, b = fit_linear(points)
    sc = sensor_cal(cal, side, idx)
    sc["mode"] = "linear"
    sc["a"] = a
    sc["b"] = b
    return a, b


def zero_from_rows(raw_rows, percentile=10.0):
    zeros = []
    for i in range(COUNT):
        values = sorted(float(row[i]) for row in raw_rows)
        if not values:
            zeros.append(0.0)
            continue
        k = int(len(values) * percentile / 100.0)
        k = clamp(k, 0, len(values) - 1)
        zeros.append(values[k])
    return zeros


def fill_zero_from_rows(cal, side, raw_rows, percentile=10.0):
    zeros = zero_from_rows(raw_rows, percentile)
    for i, z in enumerate(zeros):
        sensor_cal(cal, side, i)["zero_raw"] = z
    return zeros


def read_rows(path, sides=SIDES, as_float=False):
    """Yield (host_time, millis, side, [fsr values]) for parseable CSV rows."""
    with open(path, newline="") as handle:
        lines = [line for line in handle if not line.startswith("#")]
    for row in csv.DictReader(lines):
        side = (row.get("id") or "").strip()
        if side not in sides:
            continue
        try:
            t = float(row["host_time"])
            millis = float(row["millis"])
            conv = float if as_float else int
            vals = [conv(row[f]) for f in FSR_FIELDS]
        except (KeyError, TypeError, ValueError):
            continue
        yield t, millis, side, vals


def convert_sensor(raw, side, idx, cal):
    sc = sensor_cal(cal, side, idx)
    vcc = float(cal.get("vcc_mv", VCC_MV))
    r_fixed = float(cal.get("r_fixed_ohm", R_FIXED_OHM))

    zero = float(sc.get("zero_raw", 0.0) or 0.0)
    raw_adj = max(0.0, float(raw) - zero)
    mv = raw_to_mv(raw_adj, vcc)
    r_ohm = mv_to_resistance(mv, vcc, r_fixed)
    g_us = min(resistance_to_conductance_us(r_ohm), G_MAX_US)

    if sc.get("mode") == "linear" and sc.get("a") is not None:
        force = float(sc["a"]) * g_us + float(sc.get("b", 0.0))
        force = clamp(force, 0.0, FORCE_CAP_N)
    else:
        force = clamp(approx_force_n(g_us), 0.0, FORCE_CAP_N)

    return {"force_n": force, "raw_adj": raw_adj, "g_us": g_us}


def raw_to_conductance(raw, side, idx, cal):
    return convert_sensor(raw, side, idx, cal)["g_us"]
