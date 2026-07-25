#!/usr/bin/env python3
"""Every time a message's payload changes, print when and which bytes moved.

    python3 transitions.py route.csv 0x620 [0x638 ...] [--from 140 --to 205]

The `^^` markers under a row flag the bytes that differ from the previous row,
so a physical event (a door opening, a gear change) shows up as the same byte(s)
flipping each time. Narrate the car on a time-synced audio recording, then read
the timestamps here against the narration to name the bit.
"""
import sys
import canlog


def dump(addr, msgs, t0, t1):
  print(f"=== 0x{addr:03x} ({len(msgs)} frames) ===")
  prev = None
  for t, d in msgs:
    if t < t0 or t > t1:
      continue
    if prev is None or d != prev:
      mark = "".join("^^" if prev and i < len(prev) and d[i] != prev[i] else "  "
                     for i in range(len(d)))
      print(f"{t:9.2f}  {d.hex()}  {mark}")
      prev = d


def main(argv):
  path = argv[0]
  t0 = float(argv[argv.index("--from") + 1]) if "--from" in argv else float("-inf")
  t1 = float(argv[argv.index("--to") + 1]) if "--to" in argv else float("inf")
  addrs = [int(x, 0) for x in argv[1:] if not x.startswith("--")
           and argv[argv.index(x) - 1] not in ("--from", "--to")]
  data = canlog.load(path, addrs=addrs)
  for a in addrs:
    dump(a, data.get(a, []), t0, t1)


if __name__ == "__main__":
  if len(sys.argv) < 3:
    sys.exit(__doc__)
  main(sys.argv[1:])
