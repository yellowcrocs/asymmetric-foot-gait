"""Shared per-cycle feature extraction.

One implementation of the stance-window features, consumed by both the
offline report (analyze.py) and the live rolling-window badge
(asymmetry_live.py), so the two can never silently disagree.
"""

import numpy as np


def integrate(y, x):
    if hasattr(np, "trapezoid"):
        return float(np.trapezoid(y, x))
    return float(np.trapz(y, x))


def loading_rate(y, x):
    if len(x) < 3:
        return 0.0
    if np.all(np.diff(x) > 0):
        grad = np.gradient(y, x)
    else:
        grad = np.gradient(y)
    window = x <= x[0] + 0.15
    return float(grad[window].max()) if window.any() else float(grad.max())


def cycle_features(t, forces, total, cx, cy, start_t, end_t, prev_start=None):
    """Features for one stance window.

    t, total, cx, cy: 1-D arrays covering exactly [start_t, end_t].
    forces: (n, sensor_count) array for the same window.
    prev_start: previous stance start, or None for the first cycle.
    Returns a dict, or None when the window is degenerate.
    """
    if len(t) < 3:
        return None
    stance = end_t - start_t
    peak_n = float(total.max())
    impulse = integrate(total, t)
    loading = loading_rate(total, t)

    clipped = np.clip(forces, 0.0, None)
    load_sum = float(clipped.sum())
    if load_sum > 1e-9:
        heel_share = float(clipped[:, 0].sum() / load_sum)
        forefoot_share = float(clipped[:, 1:].sum() / load_sum)
    else:
        heel_share = None
        forefoot_share = None

    mask = np.isfinite(cx) & np.isfinite(cy)
    if mask.sum() >= 2:
        dx = np.diff(cx[mask])
        dy = np.diff(cy[mask])
        cop_path = float(np.hypot(dx, dy).sum())
        cop_ml = float(cx[mask].max() - cx[mask].min())
        cop_ap = float(cy[mask].max() - cy[mask].min())
        mean_cx = float(cx[mask].mean())
        mean_cy = float(cy[mask].mean())
    else:
        cop_path = cop_ml = cop_ap = mean_cx = mean_cy = None

    if prev_start is not None:
        stride = start_t - prev_start
        swing = stride - stance
        if stride < 0.3 or stride > 3.0 or swing < 0.05 or swing > 2.5:
            stride = swing = None
    else:
        stride = swing = None

    return {
        "stance": stance,
        "swing": swing,
        "stride": stride,
        "peak": peak_n,
        "impulse": impulse,
        "loading_rate": loading,
        "heel_share": heel_share,
        "forefoot_share": forefoot_share,
        "cop_path": cop_path,
        "cop_ml_range": cop_ml,
        "cop_ap_range": cop_ap,
        "mean_cop_x": mean_cx,
        "mean_cop_y": mean_cy,
    }
