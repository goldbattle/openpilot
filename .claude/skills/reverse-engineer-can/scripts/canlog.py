"""Shared loader for cabana-exported CAN CSVs.

CSV format (cabana File > Save As... CSV, or scripts/extract_csv.py):
    time,addr,bus,data
    15.460,0x440,1,0x4000800000000000

Every script here imports load() so the parse lives in exactly one place.
"""
import csv
import collections


def load(path, addrs=None, bus=None):
  """Return {addr: [(time_s, payload_bytes), ...]} sorted by time.

  addrs: optional iterable of ints to keep (default all).
  bus:   optional bus number to keep (default all).
  """
  keep = set(addrs) if addrs is not None else None
  out = collections.defaultdict(list)
  with open(path, newline="") as f:
    for r in csv.DictReader(f):
      a = int(r["addr"], 16)
      if keep is not None and a not in keep:
        continue
      if bus is not None and int(r["bus"]) != bus:
        continue
      d = r["data"]
      out[a].append((float(r["time"]), bytes.fromhex(d[2:] if d.startswith("0x") else d)))
  for a in out:
    out[a].sort(key=lambda x: x[0])
  return out


def duration(data):
  """(t_first, t_last) across every message in a load() dict."""
  ts = [t for msgs in data.values() for t, _ in msgs]
  return (min(ts), max(ts)) if ts else (0.0, 0.0)


if __name__ == "__main__":
  # self-check on a synthetic 2-frame CSV
  import tempfile, os
  fd, p = tempfile.mkstemp(suffix=".csv")
  os.write(fd, b"time,addr,bus,data\n1.0,0x2c1,0,0x00ff\n0.5,0x2c1,1,0xdead\n")
  os.close(fd)
  d = load(p)
  assert d[0x2c1][0] == (0.5, b"\xde\xad"), d[0x2c1]
  assert load(p, bus=0)[0x2c1] == [(1.0, b"\x00\xff")]
  assert duration(d) == (0.5, 1.0)
  os.unlink(p)
  print("canlog self-check OK")
