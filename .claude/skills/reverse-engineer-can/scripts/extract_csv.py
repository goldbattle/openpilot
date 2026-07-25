#!/usr/bin/env python3
"""Turn a comma route (rlog segments) into a cabana-format CSV, headless.

Cabana's GUI exports the same columns (File > Save As... > CSV), but this needs
no display and no upload -- point it at a folder of rlog segments, or a single
rlog, or an openpilotci/comma URL.

    # a folder holding <route>--0/rlog.zst, <route>--1/rlog.zst, ...
    # (point at the parent route dir to pool ALL segments; a single trailing
    #  segment can be almost CAN-empty if the engine was already off)
    python3 extract_csv.py /path/to/route_dir out.csv

    # a single segment or a URL
    python3 extract_csv.py '0000001f--b5778625ac--0/rlog.zst' out.csv
    python3 extract_csv.py 'https://.../rlog.zst' out.csv

Output columns match cabana exactly:  time,addr,bus,data
`time` is seconds from the first CAN frame. `bus` is the `src` field. Feed the
CSV straight into inventory.py / transitions.py / window.py.

Run with the repo venv active (needs `import opendbc`).
"""
import os
import sys
import glob

from opendbc.car.logreader import LogReader


def rlog_paths(arg):
  if arg.startswith("http") or os.path.isfile(arg):
    return [arg]
  # a directory: take rlog(.zst) from every segment, in segment order
  found = sorted(glob.glob(os.path.join(arg, "**", "rlog*"), recursive=True))
  found = [p for p in found if os.path.basename(p).startswith("rlog")]
  if not found:
    sys.exit(f"no rlog files under {arg}")
  return found


def main(src, out):
  t0 = None
  rows = 0
  with open(out, "w") as f:
    f.write("time,addr,bus,data\n")
    for path in rlog_paths(src):
      # only_union_types skips malformed events whose .which() would raise
      for evt in LogReader(path, only_union_types=True):
        if evt.which() != "can":
          continue
        t = evt.logMonoTime / 1e9
        t0 = t if t0 is None else t0
        for c in evt.can:
          f.write(f"{t - t0:.3f},0x{c.address:x},{c.src},0x{c.dat.hex()}\n")
          rows += 1
  print(f"wrote {rows} CAN frames to {out}")


if __name__ == "__main__":
  if len(sys.argv) != 3:
    sys.exit(__doc__)
  main(sys.argv[1], sys.argv[2])
