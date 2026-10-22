"""Decode a Walksnail Avatar/Ascent .osd recording into a CSV time series.

The goggles / VRX record the Betaflight on-screen display in a separate .osd file
next to the video (AscentG0020.mp4 + AscentG0020.osd). The file stores the full
character grid of the OSD several times per second. This script reads each frame,
finds the OSD elements by their Betaflight font symbols, and writes one CSV row per frame.

File layout (as observed on Walksnail files recorded with Betaflight):
    header, 40 bytes: b"BTFL" (firmware), padding, then grid width and height (uint16 LE) at offsets 36 and 38
    frames, repeated: uint32 LE timestamp in ms, then width * height uint16 LE glyph codes, row-major

Elements decoded (when present on the OSD): flight mode, throttle %, current (A), battery voltage,
distance to home (m), GPS satellites, RSSI (dBm), on-time (s). Add any element you need to OSD_PATTERNS.

Usage:
    python osd_decoder.py AscentG0020.osd -o test1.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import struct
from pathlib import Path

HEADER_SIZE = 40

# Betaflight font symbols, written in the decoded text as <xx> (hex glyph code)
OSD_PATTERNS = {
    "home_m": (re.compile(r"<11>\s*(\d+)<0c>"), int),          # home flag, distance, metre symbol
    "sats": (re.compile(r"<1e><1f>\s*(\d+)"), int),            # satellite symbol
    "amps": (re.compile(r"([\d.]+)<9a>"), float),              # value, amp symbol
    "throttle_pct": (re.compile(r"<04>\s*(\d+)"), int),        # throttle symbol
    "volt": (re.compile(r"<91>([\d.]+)<06>"), float),          # battery symbol, value, volt symbol
    "rssi_dbm": (re.compile(r"<01>(-?\d+)"), int),             # RSSI symbol
}
ONTIME = re.compile(r"<9b>(\d+):(\d+)")                        # on-time symbol, mm:ss
MODE = re.compile(r"\b(ACRO|AIR|ANGL|HOR|POSH|ALTH|RESC|AUTO|WAYP|PASS|!FS!|HEAD)\w*")


def read_frames(path: Path):
    """Yield (timestamp_ms, glyphs) for each frame of a .osd file."""
    data = path.read_bytes()
    if data[:4] != b"BTFL":
        raise ValueError(f"{path}: not a Betaflight OSD recording (header {data[:4]!r})")
    width, height = struct.unpack_from("<HH", data, 36)
    cells = width * height
    frame_size = 4 + 2 * cells
    offset = HEADER_SIZE
    while offset + frame_size <= len(data):
        (timestamp,) = struct.unpack_from("<I", data, offset)
        glyphs = struct.unpack_from(f"<{cells}H", data, offset + 4)
        yield timestamp, glyphs, width, height
        offset += frame_size


def grid_text(glyphs, width: int, height: int) -> str:
    """Printable characters stay as they are; symbols become <xx>."""
    rows = []
    for r in range(height):
        row = glyphs[r * width:(r + 1) * width]
        rows.append("".join(chr(g) if 32 <= g < 127 else f"<{g:02x}>" for g in row))
    return "\n".join(rows)


def decode_frame(text: str) -> dict:
    out = {}
    for key, (pattern, cast) in OSD_PATTERNS.items():
        m = pattern.search(text)
        out[key] = cast(m.group(1)) if m else None
    m = ONTIME.search(text)
    out["ontime_s"] = int(m.group(1)) * 60 + int(m.group(2)) if m else None
    m = MODE.search(text)
    out["mode"] = m.group(0) if m else ""
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("osd", type=Path, help="Walksnail .osd file")
    ap.add_argument("-o", "--out", type=Path, help="CSV output (default: next to the .osd file)")
    ap.add_argument("--dump", type=float, help="print the OSD grid of the frame closest to this time (s) and exit")
    args = ap.parse_args()

    if args.dump is not None:
        best = min(read_frames(args.osd), key=lambda f: abs(f[0] / 1000 - args.dump))
        print(grid_text(best[1], best[2], best[3]))
        return

    out = args.out or args.osd.with_suffix(".csv")
    fields = ["t_s", "mode", "throttle_pct", "amps", "volt", "home_m", "sats", "rssi_dbm", "ontime_s"]
    n = 0
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for timestamp, glyphs, width, height in read_frames(args.osd):
            row = decode_frame(grid_text(glyphs, width, height))
            row["t_s"] = timestamp / 1000
            w.writerow(row)
            n += 1
    print(f"{n} frames -> {out}")


if __name__ == "__main__":
    main()
