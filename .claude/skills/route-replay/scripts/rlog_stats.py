#!/usr/bin/env python3
"""Summarise a recorded route: which services logged, at what rate, and whether the car
decode actually worked.

This is the "did the drive record correctly" check. It answers, without opening a GUI:
did every service publish, is canValid holding, did ignition stay asserted, were there
gaps, and which CAN addresses were seen.

Needs openpilot importable, so run it in WSL or on the device -- not on Windows.

  python rlog_stats.py /path/to/route
  python rlog_stats.py /path/to/route --canvalid --ignition
  python rlog_stats.py /path/to/route --field carState.vEgo --field carState.gearShifter
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from openpilot.tools.lib.logreader import LogReader

# Services whose absence means something specific went wrong, and what.
EXPECTED = {
  "can": "pandad is not receiving CAN",
  "carState": "card is not running or the platform did not load",
  "modelV2": "modeld is not running (only runs onroad)",
  "roadCameraState": "camerad road camera is down",
  "driverStateV2": "dmonitoringmodeld is down (or RecordFront is off)",
  "deviceState": "hardwared is down -- the device is barely alive",
  "pandaStates": "pandad is down",
  "accelerometer": "sensord is down",
  "gpsLocationExternal": "no GPS daemon (ubloxd/qcomgpsd)",
}


def getpath(obj, dotted: str):
  for part in dotted.split(".")[1:]:
    obj = getattr(obj, part)
  return obj


def main() -> int:
  p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  p.add_argument("route")
  p.add_argument("--canvalid", action="store_true", help="carState.canValid rate + first false")
  p.add_argument("--ignition", action="store_true", help="ignitionCan/Line transitions")
  p.add_argument("--can-addrs", action="store_true", help="unique CAN addresses per bus")
  p.add_argument("--field", action="append", default=[],
                 help="service.field to tally distinct values, e.g. carState.gearShifter")
  p.add_argument("--gap", type=float, default=2.0, help="report gaps longer than this (s)")
  a = p.parse_args()

  counts: Counter = Counter()
  first_t: dict[str, float] = {}
  last_t: dict[str, float] = {}
  gaps: dict[str, list[tuple[float, float]]] = defaultdict(list)
  field_vals: dict[str, Counter] = defaultdict(Counter)
  canvalid = Counter()
  first_invalid: float | None = None
  ign_trans: list[tuple[float, bool, bool]] = []
  prev_ign: tuple[bool, bool] | None = None
  addrs: dict[int, set] = defaultdict(set)
  t0: float | None = None

  for msg in LogReader(a.route):
    w = msg.which()
    t = msg.logMonoTime * 1e-9
    if t0 is None:
      t0 = t
    counts[w] += 1
    first_t.setdefault(w, t)
    if w in last_t and t - last_t[w] > a.gap:
      gaps[w].append((last_t[w] - t0, t - last_t[w]))
    last_t[w] = t

    if w == "carState":
      cs = msg.carState
      canvalid[bool(cs.canValid)] += 1
      if not cs.canValid and first_invalid is None:
        first_invalid = t - t0
    elif w == "pandaStates" and len(msg.pandaStates):
      ps = msg.pandaStates[0]
      cur = (bool(ps.ignitionLine), bool(ps.ignitionCan))
      if cur != prev_ign:
        ign_trans.append((t - t0, cur[0], cur[1]))
        prev_ign = cur
    elif w == "can" and a.can_addrs:
      for f in msg.can:
        addrs[f.src].add(f.address)

    for spec in a.field:
      if spec.split(".")[0] == w:
        try:
          v = getpath(msg, spec)
        except Exception:
          continue
        field_vals[spec][str(round(v, 1) if isinstance(v, float) else v)] += 1

  if t0 is None:
    print("empty route (no messages)", file=sys.stderr)
    return 1

  span = max(last_t.values()) - t0
  print(f"route span {span:.1f}s, {sum(counts.values())} messages, {len(counts)} services\n")

  print(f"{'service':<26}{'count':>8}{'Hz':>8}   gaps")
  for svc, n in sorted(counts.items(), key=lambda kv: -kv[1]):
    g = gaps.get(svc, [])
    gtxt = f"{len(g)} > {a.gap}s (worst {max(x[1] for x in g):.1f}s)" if g else ""
    print(f"{svc:<26}{n:>8}{n/max(span,1e-6):>8.1f}   {gtxt}")

  missing = [s for s in EXPECTED if s not in counts]
  if missing:
    print("\nMISSING services:")
    for s in missing:
      print(f"  {s:<24} -> {EXPECTED[s]}")

  if a.canvalid or canvalid:
    tot = sum(canvalid.values())
    if tot:
      pct = 100.0 * canvalid[True] / tot
      print(f"\ncanValid: {canvalid[True]}/{tot} ({pct:.1f}%)")
      if pct < 95:
        print(f"  first false at t+{first_invalid:.1f}s")
        print("  Almost always the DBC, not the wiring. Check for a signal named COUNTER or")
        print("  CHECKSUM that isn't one -- see the car-port-passive skill.")

  if a.ignition:
    print(f"\nignition transitions ({len(ign_trans)}):")
    for t, line, can in ign_trans[:40]:
      print(f"  t+{t:>7.1f}s  ignitionLine={int(line)}  ignitionCan={int(can)}")
    if not ign_trans:
      print("  none -- ignition never changed for the whole route")

  if a.can_addrs:
    print("\nCAN addresses per bus:")
    for bus in sorted(addrs):
      lst = sorted(addrs[bus])
      print(f"  bus {bus}: {len(lst)} addrs  {[hex(x) for x in lst[:24]]}"
            f"{' ...' if len(lst) > 24 else ''}")

  for spec, vals in field_vals.items():
    print(f"\n{spec}: {len(vals)} distinct  {dict(vals.most_common(8))}")

  return 0


if __name__ == "__main__":
  sys.exit(main())
