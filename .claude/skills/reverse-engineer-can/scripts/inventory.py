#!/usr/bin/env python3
"""What is on this bus? One line per message: count, rate, and per-byte activity.

    python3 inventory.py route.csv [--bus 0]

Per byte:  b3=const1c  = never changed (0x1c the whole log)
           b2:107v     = took 107 distinct values (a live signal or counter)
Constant bytes are usually padding or ECU IDs; high-variety bytes are where the
signals live. Start here, then aim transitions.py / window.py at the busy ones.
"""
import sys
import collections
import canlog


def main(path, bus=None):
  data = canlog.load(path, bus=bus)
  t0, t1 = canlog.duration(data)
  span = max(t1 - t0, 1e-9)
  print(f"# {path}  {t0:.1f}-{t1:.1f}s ({span:.0f}s), {len(data)} message IDs")
  for a in sorted(data):
    msgs = data[a]
    n = len(msgs[0][1])
    cols = [collections.Counter() for _ in range(n)]
    for _, b in msgs:
      for i, x in enumerate(b):
        cols[i][x] += 1
    desc = []
    for i, c in enumerate(cols):
      desc.append(f"b{i}=const{next(iter(c)):02x}" if len(c) == 1 else f"b{i}:{len(c)}v")
    print(f"0x{a:03x} ({a:4d})  {len(msgs):6d} frames  {len(msgs)/span:6.1f} Hz  {' '.join(desc)}")


if __name__ == "__main__":
  if len(sys.argv) < 2:
    sys.exit(__doc__)
  b = int(sys.argv[sys.argv.index("--bus") + 1]) if "--bus" in sys.argv else None
  main(sys.argv[1], b)
