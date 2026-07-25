#!/usr/bin/env python3
"""Isolate the byte that a narrated action moved.

    python3 window.py route.csv 142 200        # hood test ran 142-200s

Prints every byte that changed INSIDE the window, alongside the values it took
OUTSIDE it. A byte marked `<-- UNIQUE` took values in the window that never
appear anywhere else in the log -- that is your signal for the narrated action.
A byte busy both in and out (wheel speeds, RPM, counters) is noise for this test;
suppress the worst offenders with --ignore.

Workflow: transcribe the calibration audio (see SKILL.md), read off the start/end
seconds of one narrated action ("now I'm opening the trunk ... now closing it"),
window on it, and the UNIQUE byte is the message+byte that action lives in. Then
confirm the exact bit with transitions.py over the same window.
"""
import sys
import collections
import canlog


def main(path, t0, t1, ignore=()):
  data = canlog.load(path)
  ignore = set(ignore)
  for a in sorted(data):
    if a in ignore:
      continue
    inside = collections.defaultdict(set)
    outside = collections.defaultdict(set)
    for t, b in data[a]:
      tgt = inside if t0 <= t <= t1 else outside
      for i, x in enumerate(b):
        tgt[i].add(x)
    for i in sorted(inside):
      if len(inside[i]) > 1:
        unique = inside[i] - outside[i]
        ins = sorted(hex(v) for v in inside[i])
        out = sorted(hex(v) for v in outside[i])
        print(f"0x{a:03x} b{i}: in-window {ins}  outside {out}"
              f"{'   <-- UNIQUE' if unique else ''}")


if __name__ == "__main__":
  if len(sys.argv) < 4:
    sys.exit(__doc__)
  ig = [int(x, 0) for x in sys.argv[sys.argv.index("--ignore") + 1:]] if "--ignore" in sys.argv else []
  main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), ig)
