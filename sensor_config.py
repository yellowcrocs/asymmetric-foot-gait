"""Shared sensor layout for the FootGait insoles."""

SENSOR_LABELS = ["Heel", "Ball - inner", "Ball - outer", "Toes"]

SENSOR_POS = [
    (0.55, 0.86, "auto"),
    (0.37, 0.30, "in"),
    (0.64, 0.30, "out"),
    (0.45, 0.10, "auto"),
]

POS_XY = [(x, 1.0 - y) for x, y, _ in SENSOR_POS]

NODES = [
    (SENSOR_LABELS[i], SENSOR_POS[i][0], SENSOR_POS[i][1], SENSOR_POS[i][2])
    for i in range(len(SENSOR_LABELS))
]
