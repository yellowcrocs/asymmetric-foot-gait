"""Capture raw FSR values from the serial calibration sketch.

Flash firmware/calibrate_serial first, then:

    python serial_calibrate.py --port COM8 --seconds 6

Prints the live lines and the median raw value per sensor. Use the medians for
calibrate.py zero/add.
"""

import argparse
import statistics
import time

import serial

import processing as proc


def main():
    parser = argparse.ArgumentParser(description="Read raw FSR values over serial")
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=float, default=6.0)
    args = parser.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=0.2)
    time.sleep(0.6)

    samples = []
    end = time.time() + args.seconds
    try:
        while time.time() < end:
            line = ser.readline()
            if not line:
                continue
            text = line.decode("utf-8", "replace").strip()
            if not text or text.startswith("#"):
                continue
            parts = text.split(",")
            if len(parts) < proc.COUNT + 1:
                continue
            try:
                values = [int(p) for p in parts[1:1 + proc.COUNT]]
            except ValueError:
                continue
            samples.append(values)
            print(text)
    finally:
        ser.close()

    if not samples:
        print("no samples")
        return
    medians = [statistics.median(s[i] for s in samples) for i in range(proc.COUNT)]
    print(f"samples {len(samples)}")
    print("median_raw " + ",".join(str(int(round(m))) for m in medians))


if __name__ == "__main__":
    main()
