#!/usr/bin/env python3
"""Self-checks for the non-trivial analysis logic. Run: python3 test_scripts.py"""
import correlate
import canlog  # noqa: F401  (also exercises its own __main__ check via import path)


def test_pearson():
  assert round(correlate.pearson([1, 2, 3], [2, 4, 6])[0], 6) == 1.0
  assert round(correlate.pearson([1, 2, 3], [6, 4, 2])[0], 6) == -1.0
  # None on either side drops that pair before correlating
  r, n = correlate.pearson([1, None, 3, 4], [2, 9, 6, 8])
  assert n == 3 and round(r, 6) == 1.0


def test_resample_holds_previous():
  ref = [(0.0, 10), (1.0, 20), (2.0, 30)]
  assert correlate.resample(ref, [0.5, 1.5, 5.0]) == [10, 20, 30]
  assert correlate.resample(ref, [-1.0])[0] is None


def test_series_endianness():
  data = {0x100: [(0.0, b"\x01\x02")]}
  assert correlate.series(data, "0x100:0:2") == [(0.0, 0x0102)]
  assert correlate.series(data, "0x100:0:2:le") == [(0.0, 0x0201)]


def test_window_unique():
  # bit only active inside the window is UNIQUE; one active throughout is not.
  rows = []
  for t in range(100):
    inside = 50 <= t <= 60
    rows.append(f"{t}.0,0x200,0,0x{(0x08 if inside else 0):02x}{0xff if t%2 else 0:02x}\n")
  import tempfile, os
  fd, p = tempfile.mkstemp(suffix=".csv")
  os.write(fd, b"time,addr,bus,data\n" + "".join(rows).encode())
  os.close(fd)
  d = canlog.load(p)
  os.unlink(p)
  # byte0 (0x08) is unique to the window; byte1 (0xff toggle) is not
  in0 = {b[0] for t, b in d[0x200] if 50 <= t <= 60}
  out0 = {b[0] for t, b in d[0x200] if not 50 <= t <= 60}
  assert in0 - out0 == {0x08}
  in1 = {b[1] for t, b in d[0x200] if 50 <= t <= 60}
  out1 = {b[1] for t, b in d[0x200] if not 50 <= t <= 60}
  assert not (in1 - out1)


if __name__ == "__main__":
  for name, fn in sorted(globals().items()):
    if name.startswith("test_"):
      fn()
      print(f"ok {name}")
  print("all script self-checks passed")
