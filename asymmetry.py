"""Bilateral asymmetry metrics for FootGait.

The core index is the Robinson symmetry index (SI):

    SI = 100 * (L - R) / (0.5 * (L + R))

Positive SI means the left value is larger; negative means right is larger.
Thresholds are provisional and must be tuned on real subjects.
"""

import math
from typing import NamedTuple

import numpy as np

TEMPORAL_THRESHOLD = 15.0
KINETIC_THRESHOLD = 17.0
SPATIAL_THRESHOLD = 30.0


class Metric(NamedTuple):
    group: str
    threshold: float
    weight: float
    unit: str


METRIC_DEFS = {
    "stance": Metric("temporal", TEMPORAL_THRESHOLD, 0.16, "s"),
    "swing": Metric("temporal", TEMPORAL_THRESHOLD, 0.16, "s"),
    "stride": Metric("temporal", TEMPORAL_THRESHOLD, 0.06, "s"),
    "step": Metric("temporal", TEMPORAL_THRESHOLD, 0.08, "s"),
    "peak": Metric("kinetic", KINETIC_THRESHOLD, 0.17, "N"),
    "impulse": Metric("kinetic", KINETIC_THRESHOLD, 0.17, "N*s"),
    "heel_share": Metric("kinetic", KINETIC_THRESHOLD, 0.08, "fraction"),
    "cop_path": Metric("spatial", SPATIAL_THRESHOLD, 0.06, "normalized"),
    "cop_ml_range": Metric("spatial", SPATIAL_THRESHOLD, 0.06, "normalized"),
}


def si(left, right):
    if left is None or right is None:
        return None
    denom = 0.5 * (left + right)
    if abs(denom) < 1e-12:
        return 0.0
    return 100.0 * (left - right) / denom


def plotnik_ga(left, right):
    if not left or not right or left <= 0 or right <= 0:
        return None
    return 100.0 * abs(math.log(left / right))


def bootstrap_ci(left_values, right_values, n=2000, alpha=0.05, seed=7):
    left = [v for v in left_values if v is not None and np.isfinite(v)]
    right = [v for v in right_values if v is not None and np.isfinite(v)]
    if len(left) < 2 or len(right) < 2:
        return (None, None)
    rng = np.random.default_rng(seed)
    left_arr = np.asarray(left, dtype=float)
    right_arr = np.asarray(right, dtype=float)
    stats = np.empty(n)
    for i in range(n):
        l = rng.choice(left_arr, len(left_arr), replace=True)
        r = rng.choice(right_arr, len(right_arr), replace=True)
        stats[i] = si(float(np.median(l)), float(np.median(r)))
    lo = float(np.percentile(stats, 100.0 * alpha / 2.0))
    hi = float(np.percentile(stats, 100.0 * (1.0 - alpha / 2.0)))
    return (lo, hi)


def flag_metric(si_value, ci, threshold):
    if si_value is None or ci is None or ci[0] is None or ci[1] is None:
        return False
    excludes_zero = ci[0] > 0.0 or ci[1] < 0.0
    return bool(excludes_zero and abs(si_value) > threshold)


def composite(metrics, noise_floor=0.0):
    """Weighted exceedance over thresholds.

    ``noise_floor`` (0..1) discounts a fraction of each metric's normalized
    exceedance. Used when hardware quality forces a conservative score.
    """
    total_weight = 0.0
    score = 0.0
    for metric in metrics.values():
        if metric.get("si") is None:
            continue
        weight = float(metric.get("weight", 0.0))
        threshold = float(metric.get("threshold", 1.0))
        total_weight += weight
        excess = abs(metric["si"]) / threshold - noise_floor
        score += weight * min(max(excess, 0.0), 1.0)
    if total_weight <= 0.0:
        return 0.0
    return 100.0 * score / total_weight


def classify(metrics, score):
    flags = 0
    weighted_sign = 0.0
    for metric in metrics.values():
        if metric.get("si") is None:
            continue
        if metric.get("flag"):
            flags += 1
        sign = 1.0 if metric["si"] > 0 else -1.0 if metric["si"] < 0 else 0.0
        weighted_sign += float(metric.get("weight", 0.0)) * sign

    if weighted_sign > 0:
        side = "L larger"
    elif weighted_sign < 0:
        side = "R larger"
    else:
        side = "even"

    asymmetric = bool(flags >= 2 and score >= 45.0)
    if not asymmetric:
        severity = "symmetric" if score < 45.0 else "borderline"
    elif score < 60.0:
        severity = "mild"
    elif score < 75.0:
        severity = "moderate"
    else:
        severity = "severe"

    return {
        "asymmetric": asymmetric,
        "score": score,
        "severity": severity,
        "side": side,
        "driving_metrics": [name for name, m in metrics.items() if m.get("flag")],
        "flagged_count": flags,
    }
