"""
FootGait - live UDP logger + terminal dashboard.

Receives frames from the two ESP32 foot nodes:

    seq,id,millis,fsr0,fsr1,fsr2,fsr3

Replies to every frame with "ACK:<id>:<seq>" (unicast to the sender),
writes a master CSV, and shows a live four-node readout per foot:

    Heel         : 1234
    Ball - inner :  450
    Ball - outer :  380
    Toes         :  900

Run:
    python logger.py
    python logger.py --port 4210
    python logger.py --no-log
"""

import argparse
import ctypes
import os
import socket
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import sensor_config as sc

NODE_LABELS = sc.SENSOR_LABELS
LAYOUT_W = 42
STALE_S = 2.0

CSV_HEADER = ("host_time,id,seq,millis,"
              + ",".join(f"fsr{i}" for i in range(len(NODE_LABELS))) + "\n")


def format_row(now, dev, seq, millis, vals):
    return f"{now:.3f},{dev},{seq},{millis}," + ",".join(str(v) for v in vals) + "\n"


def enable_ansi():
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        kernel32.SetConsoleMode(handle, 7)
    except Exception:
        os.system("")


def new_state():
    return {
        "vals": [None, None, None, None],
        "last_t": 0.0,
        "last_seq": None,
        "last_millis": 0,
        "count": 0,
        "drops": 0,
        "times": deque(maxlen=512),
        "rate": 0.0,
    }


STATE = {"L": new_state(), "R": new_state()}


def update_rate(st, now):
    cutoff = now - 1.0
    while st["times"] and st["times"][0] < cutoff:
        st["times"].popleft()
    st["rate"] = float(len(st["times"]))


def handle_frame(text, addr, sock, sock_ok):
    parts = [p.strip() for p in text.strip().split(",")]
    if len(parts) < 7:
        return
    try:
        seq = int(parts[0])
        dev = parts[1]
        millis = int(parts[2])
        vals = [int(p) for p in parts[3:7]]
    except ValueError:
        return

    st = STATE.get(dev)
    if st is None:
        return

    if sock_ok:
        try:
            sock.sendto(f"ACK:{dev}:{seq}".encode("ascii"), addr)
        except OSError:
            pass

    now = time.time()
    if st["last_seq"] is not None and seq > st["last_seq"] + 1:
        st["drops"] += seq - st["last_seq"] - 1
    st["last_seq"] = seq
    st["last_t"] = now
    st["last_millis"] = millis
    st["count"] += 1
    st["vals"] = vals
    st["times"].append(now)
    return now, dev, seq, millis, vals


def foot_block(dev, st, now):
    title = f"{'LEFT' if dev == 'L' else 'RIGHT'} FOOT (id={dev})"
    out = [title]
    for label, val in zip(NODE_LABELS, st["vals"]):
        shown = f"{val:5d}" if val is not None else "  ---"
        out.append(f"{label:<13}: {shown}")

    if st["last_t"]:
        age = now - st["last_t"]
        if age > STALE_S:
            out.append(f"STALE ({age:.1f}s) - waiting")
        else:
            out.append(f"updated {age:.1f}s ago")
    else:
        out.append("waiting for data...")
    return out


def render(log_path, sock_ok, now):
    l, r = STATE["L"], STATE["R"]
    header = (
        "FootGait LIVE   "
        f"rate  L:{l['rate']:5.1f} pkt/s  R:{r['rate']:5.1f} pkt/s   "
        f"drops  L:{l['drops']:<5d} R:{r['drops']:<5d}"
    )
    left = foot_block("L", l, now)
    right = foot_block("R", r, now)

    lines = [header, ""]
    for lf, rf in zip(left, right):
        lines.append(lf.ljust(LAYOUT_W) + rf)
    lines.append("")
    lines.append(f"log: {log_path if log_path else '(disabled)'}")
    if not sock_ok:
        lines.append("UDP socket unavailable - not receiving.")
    lines.append("Ctrl+C to stop.")

    sys.stdout.write("\x1b[H\x1b[J" + "\n".join(lines) + "\n")
    sys.stdout.flush()


def write_snapshot(log_dir):
    for dev in ("L", "R"):
        st = STATE[dev]
        if st["last_t"] == 0:
            continue
        path = log_dir / f"last_{dev}.csv"
        with open(path, "w", newline="") as f:
            f.write(CSV_HEADER)
            vals = ",".join(str(v) if v is not None else "" for v in st["vals"])
            f.write(f"{st['last_t']:.3f},{dev},{st['last_seq']},"
                    f"{st['last_millis']},{vals}\n")


def main():
    ap = argparse.ArgumentParser(description="FootGait live UDP logger")
    ap.add_argument("--port", type=int, default=4210, help="UDP listen port (default 4210)")
    ap.add_argument("--no-log", action="store_true", help="do not write CSV files")
    ap.add_argument("--hz", type=float, default=5.0, help="dashboard refresh rate (default 5)")
    args = ap.parse_args()

    enable_ansi()

    log_dir = Path(__file__).resolve().parent / "logs"
    log_path = None
    log_file = None
    log_buf = []

    if not args.no_log:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"master_{stamp}.csv"
        log_file = open(log_path, "w", newline="")
        log_file.write(CSV_HEADER)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if os.name != "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock_ok = True
    try:
        sock.bind(("0.0.0.0", args.port))
        sock.settimeout(0.05)
    except OSError as exc:
        sock_ok = False
        sock.close()
        sock = None
        print(f"[WARN] could not bind UDP {args.port}: {exc}")

    print(f"[INFO] listening on UDP {args.port}")
    if log_path:
        print(f"[INFO] logging to {log_path}")
    print("[INFO] waiting for foot nodes...")
    time.sleep(0.6)

    render_period = 1.0 / args.hz if args.hz > 0 else 0.2
    last_render = 0.0
    last_flush = time.time()

    try:
        while True:
            if sock_ok:
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    data = None
                except OSError:
                    data = None
                if data:
                    parsed = handle_frame(data.decode("ascii", "ignore"), addr, sock, sock_ok)
                    if parsed and log_file is not None:
                        log_buf.append(format_row(*parsed))
            else:
                time.sleep(0.1)

            now = time.time()
            if now - last_flush >= 0.5 and log_file is not None:
                if log_buf:
                    log_file.writelines(log_buf)
                    log_buf.clear()
                log_file.flush()
                last_flush = now

            if now - last_render >= render_period:
                for dev in ("L", "R"):
                    update_rate(STATE[dev], now)
                render(log_path, sock_ok, now)
                last_render = now

    except KeyboardInterrupt:
        pass
    finally:
        if log_file is not None:
            if log_buf:
                log_file.writelines(log_buf)
            log_file.flush()
            log_file.close()
            write_snapshot(log_dir)
        if sock is not None:
            sock.close()
        print("\n[INFO] stopped.")


if __name__ == "__main__":
    main()
