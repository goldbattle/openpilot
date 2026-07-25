#!/usr/bin/env python3
"""Confirm a numeric signal by correlating it against something you trust.

Extracts a byte-aligned value (one or more whole bytes) and correlates it, on a
common time base, against a second such value, or against the message period, or
against elapsed time. Byte-granular on purpose -- it covers wheel speeds, RPM,
temps, torque, fuel injection (all byte-aligned). For a sub-byte candidate, put
it in the DBC and use decode_check.py, which decodes exactly.

    # two signals track each other (mean wheel speed vs SPEED)
    python3 correlate.py route.csv 0xb4:5:2 --vs 0xb0:1:2

    # is this frame crank-synchronous? correlate its PERIOD against RPM
    python3 correlate.py route.csv 0x2c4:0:2 --period-of 0x398

    # warm-up: does it climb monotonically with time?
    python3 correlate.py route.csv 0x3b4:2:1 --vs-time --from 0 --to 300

Spec is addr:byte_offset:num_bytes, e.g. 0x2c4:0:2 = message 0x2C4, bytes 0-1,
big-endian. Add :le for little-endian.
"""
import sys
import bisect
import statistics
import canlog


def parse_spec(s):
  parts = s.split(":")
  addr = int(parts[0], 0)
  off = int(parts[1])
  n = int(parts[2])
  le = len(parts) > 3 and parts[3] == "le"
  return addr, off, n, le


def series(data, spec):
  addr, off, n, le = parse_spec(spec)
  out = []
  for t, b in data.get(addr, []):
    if off + n <= len(b):
      out.append((t, int.from_bytes(b[off:off + n], "little" if le else "big")))
  return out


def resample(ref, at_times):
  """value of ref (list of (t,v)) sampled just-before each query time."""
  ts = [t for t, _ in ref]
  out = []
  for t in at_times:
    i = bisect.bisect(ts, t) - 1
    out.append(ref[i][1] if i >= 0 else None)
  return out


def pearson(xs, ys):
  pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
  if len(pairs) < 3:
    return float("nan"), 0
  xs, ys = zip(*pairs)
  mx, my = statistics.mean(xs), statistics.mean(ys)
  num = sum((x - mx) * (y - my) for x, y in pairs)
  dx = sum((x - mx) ** 2 for x in xs) ** 0.5
  dy = sum((y - my) ** 2 for y in ys) ** 0.5
  return (num / (dx * dy) if dx and dy else float("nan")), len(pairs)


def main(argv):
  path = argv[0]
  t0 = float(argv[argv.index("--from") + 1]) if "--from" in argv else float("-inf")
  t1 = float(argv[argv.index("--to") + 1]) if "--to" in argv else float("inf")
  data = canlog.load(path)
  sig = [(t, v) for t, v in series(data, argv[1]) if t0 <= t <= t1]
  if not sig:
    sys.exit("no samples for " + argv[1])
  st = [t for t, _ in sig]
  sv = [v for _, v in sig]

  if "--period-of" in argv:
    addr = int(argv[argv.index("--period-of") + 1], 0)
    frames = [t for t, _ in data.get(addr, []) if t0 <= t <= t1]
    gaps = [(frames[i], frames[i] - frames[i - 1]) for i in range(1, len(frames))
            if frames[i] - frames[i - 1] < 3]
    rpm = resample(sig, [t for t, _ in gaps])
    # a crank-synchronous frame emits once per fixed number of revolutions:
    revs = [r / 60.0 * g for r, (_, g) in zip(rpm, gaps) if r and r > 300]
    if revs:
      print(f"revolutions per frame of 0x{addr:03x}: "
            f"median {statistics.median(revs):.2f}  mean {statistics.mean(revs):.2f}  "
            f"stdev {statistics.stdev(revs):.2f}  n={len(revs)}")
      print("(tight stdev => crank-synchronous, i.e. an engine/fuel-timing frame)")
    return

  if "--vs-time" in argv:
    r, n = pearson(st, sv)
    print(f"{argv[1]} vs time: r={r:+.4f} over n={n}  "
          f"(range {min(sv)}..{max(sv)})")
    return

  other = argv[argv.index("--vs") + 1]
  ref = series(data, other)
  rv = resample(ref, st)
  r, n = pearson(sv, rv)
  print(f"{argv[1]} vs {other}: r={r:+.4f} over n={n}")


if __name__ == "__main__":
  if len(sys.argv) < 3:
    sys.exit(__doc__)
  main(sys.argv[1:])
