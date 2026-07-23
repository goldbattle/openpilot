#!/usr/bin/env python3
"""Survey the raw CAN in a recorded route: what addresses exist, how fast, and which bytes
actually carry information.

Runs on: WSL or the device  |  Needs: openpilot importable (not Windows). On the device:
`comma.py py can_survey.py <route> --bus 1`.

This is the first thing to run on a new car, before writing a single DBC line. It answers
"what am I even looking at", and the per-byte report tells you which bytes are constant
(ignore), which toggle a couple of bits (booleans: doors, brake, key), which ramp smoothly
(sensors: speed, angle), and which really are rolling counters.

Needs openpilot importable, so run it in WSL or on the device -- not on Windows.

  python can_survey.py /path/to/route --bus 1
  python can_survey.py /path/to/route --addr 0x620          # deep-dive one message
  python can_survey.py /path/to/route --addr 0x620 --watch KEY_ON:4:5
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from openpilot.tools.lib.logreader import LogReader


def classify(values: list[int]) -> str:
  """Describe one byte position over the whole route."""
  uniq = sorted(set(values))
  n = len(values)
  if len(uniq) == 1:
    return f"constant {uniq[0]:#04x}"

  # A true rolling counter changes on essentially EVERY frame and each change is +1. Both
  # halves matter: a slowly ramping analog value also steps +1, but only on some frames, so
  # requiring a high change rate is what separates the two. (The 2010 Camry's 0x620 byte 3
  # changed on only 19% of frames and never stepped +1 -- it was named COUNTER anyway, which
  # held the message invalid for an entire drive. That is the bug this is here to catch.)
  steps = [(b - a) % 256 for a, b in zip(values, values[1:]) if a != b]
  plus_one = sum(1 for s in steps if s == 1)
  changes = len(steps)
  change_rate = changes / max(n - 1, 1)
  if changes and plus_one / changes > 0.9 and change_rate > 0.9:
    return f"COUNTER-like ({plus_one}/{changes} steps are +1, changes on {change_rate:.0%} of frames)"

  if len(uniq) <= 4:
    return f"enum/bitfield {[hex(u) for u in uniq]} ({changes} changes in {n} frames)"

  # smooth = most steps are small relative to the range: a real analog quantity
  small = sum(1 for s in steps if s in (1, 255, 2, 254))
  if changes and small / changes > 0.6:
    return (f"smooth/analog, range {uniq[0]:#04x}..{uniq[-1]:#04x} ({len(uniq)} values, "
            f"changes on {change_rate:.0%} of frames)")
  return f"varying, {len(uniq)} values, {changes} changes in {n} frames"


def main() -> int:
  p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  p.add_argument("route", help="route dir/id accepted by LogReader (or a path to rlog files)")
  p.add_argument("--bus", type=int, help="only this bus (OBD-multiplexed cars are on bus 1)")
  p.add_argument("--addr", help="deep-dive a single address, e.g. 0x620")
  p.add_argument("--watch", action="append", default=[],
                 help="NAME:BYTE:BIT -- print how often that bit is set (repeatable)")
  p.add_argument("--top", type=int, default=40)
  a = p.parse_args()

  target = int(a.addr, 0) if a.addr else None

  counts: Counter = Counter()
  buses: Counter = Counter()
  per_addr_len: dict[int, Counter] = defaultdict(Counter)
  frames_by_byte: dict[int, list[int]] = defaultdict(list)
  t_first: dict[int, float] = {}
  t_last: dict[int, float] = {}
  bit_hits: Counter = Counter()
  total = 0
  mono = 0.0

  for msg in LogReader(a.route):
    if msg.which() != "can":
      continue
    mono = msg.logMonoTime * 1e-9
    for f in msg.can:
      if a.bus is not None and f.src != a.bus:
        continue
      total += 1
      counts[f.address] += 1
      buses[f.src] += 1
      per_addr_len[f.address][len(f.dat)] += 1
      t_first.setdefault(f.address, mono)
      t_last[f.address] = mono

      if target is not None and f.address == target:
        for i, b in enumerate(f.dat):
          frames_by_byte[i].append(b)
        for spec in a.watch:
          name, byte_s, bit_s = spec.split(":")
          bi, bit = int(byte_s), int(bit_s)
          if bi < len(f.dat) and (f.dat[bi] >> bit) & 1:
            bit_hits[name] += 1

  if not total:
    print("no CAN frames matched. Wrong bus? Wrong route path?", file=sys.stderr)
    return 1

  print(f"{total} frames, {len(counts)} unique addresses, buses {dict(buses)}")

  if target is None:
    print(f"\n{'addr':>8} {'count':>8} {'Hz':>7}  dlc")
    for addr, n in counts.most_common(a.top):
      span = max(t_last[addr] - t_first[addr], 1e-6)
      dlcs = ",".join(str(d) for d in sorted(per_addr_len[addr]))
      print(f"{addr:#8x} {n:>8} {n/span:>7.1f}  {dlcs}")
    print("\nNext: pick an address and re-run with --addr to see which bytes move.")
    return 0

  n = counts[target]
  span = max(t_last[target] - t_first[target], 1e-6)
  print(f"\n=== {target:#x}: {n} frames over {span:.1f}s ({n/span:.2f} Hz) ===")
  for i in sorted(frames_by_byte):
    print(f"  byte {i}: {classify(frames_by_byte[i])}")

  for name, hits in bit_hits.items():
    print(f"\n  {name}: set in {hits}/{n} frames ({100.0*hits/n:.1f}%)")
  for spec in a.watch:
    name = spec.split(":")[0]
    if name not in bit_hits:
      print(f"\n  {name}: set in 0/{n} frames (0.0%)")
  return 0


if __name__ == "__main__":
  sys.exit(main())
