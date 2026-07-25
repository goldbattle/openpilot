#!/usr/bin/env python3
"""Replay a CSV through the real DBC parser and print what a message decodes to.

This is the ground truth: once you have written a candidate SG_ into the DBC,
decode_check confirms it decodes as you expect -- including sub-byte fields that
the byte-granular exploration scripts can't reach.

    # every distinct decoded state of the message, with its timestamp
    python3 decode_check.py route.csv toyota_camry_xv40_2010_pt DOOR_LOCKS

    # decoded values frozen at specific narrated moments
    python3 decode_check.py route.csv toyota_camry_xv40_2010_pt BODY_CONTROL_STATE --at 54.5 206 525

Run from a checkout where `python3 -c "import opendbc"` works (the repo root,
with its venv active).
"""
import os
import re
import sys
import canlog

import opendbc
from opendbc.can.parser import CANParser

DBC_DIR = os.path.join(os.path.dirname(opendbc.__file__), "dbc")


def msg_addr(dbc_name, msg_name):
  path = os.path.join(DBC_DIR, dbc_name + ".dbc")
  for line in open(path):
    m = re.match(rf"BO_ (\d+) {re.escape(msg_name)}:", line)
    if m:
      return int(m.group(1))
  sys.exit(f"{msg_name} not found in {path}")


def decoded(parser, msg_name):
  return {k: round(float(v), 3) for k, v in parser.vl[msg_name].items()}


def main(argv):
  path, dbc_name, msg_name = argv[0], argv[1], argv[2]
  addr = msg_addr(dbc_name, msg_name)
  frames = canlog.load(path, addrs={addr})[addr]

  ats = [float(x) for x in argv[argv.index("--at") + 1:]] if "--at" in argv else None
  if ats:
    for tgt in ats:
      p = CANParser(dbc_name, [(msg_name, 0)], 0)
      p.update([[int(t * 1e9), [(addr, d, 0)]] for t, d in frames if t <= tgt])
      print(f"{tgt:8.2f}  {decoded(p, msg_name)}")
    return

  p = CANParser(dbc_name, [(msg_name, 0)], 0)
  prev = None
  for t, d in frames:
    p.update([[int(t * 1e9), [(addr, d, 0)]]])
    cur = decoded(p, msg_name)
    if cur != prev:
      print(f"{t:8.2f}  {cur}")
      prev = cur


if __name__ == "__main__":
  if len(sys.argv) < 4:
    sys.exit(__doc__)
  main(sys.argv[1:])
