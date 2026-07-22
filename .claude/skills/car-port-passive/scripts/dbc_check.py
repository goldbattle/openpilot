#!/usr/bin/env python3
"""Static validation of a DBC, and cross-reference of a carstate against it.

This is the substitute for CANParser on a dev box where openpilot can't be imported
(Windows: fcntl, params_pyx; CANParser used to be a compiled extension). It catches the
classes of bug that otherwise only surface as a silently-invalid message on the car.

  python dbc_check.py opendbc_repo/opendbc/dbc/toyota_camry_xv40_2010_pt.dbc
  python dbc_check.py <dbc> --carstate opendbc_repo/opendbc/car/toyota_xv40/carstate.py
  python dbc_check.py --self-check
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

BO_RE = re.compile(r'^BO_\s+(\d+)\s+([A-Za-z0-9_]+)\s*:\s*(\d+)\s+(\S+)')
# SG_ <name> [M|m<n>] : <start>|<len>@<endian><sign> (<factor>,<offset>) [<min>|<max>] "<unit>" <recv>
SG_RE = re.compile(r'^\s*SG_\s+([A-Za-z0-9_]+)\s*(?:[Mm]\d*\s*)?:\s*'
                   r'(\d+)\|(\d+)@([01])([+-])\s*\(([^,]+),([^)]+)\)')
VL_RE = re.compile(r"""\bvl\s*\[\s*["']([A-Za-z0-9_]+)["']\s*\]\s*\[\s*["']([A-Za-z0-9_]+)["']\s*\]""")


class Signal:
  __slots__ = ("name", "start", "length", "big_endian", "signed", "factor", "offset")

  def __init__(self, name, start, length, big_endian, signed, factor, offset):
    self.name, self.start, self.length = name, start, length
    self.big_endian, self.signed = big_endian, signed
    self.factor, self.offset = factor, offset


class Message:
  __slots__ = ("address", "name", "dlc", "signals")

  def __init__(self, address, name, dlc):
    self.address, self.name, self.dlc = address, name, dlc
    self.signals: list[Signal] = []


def parse_dbc(text: str) -> list[Message]:
  msgs: list[Message] = []
  for line in text.splitlines():
    m = BO_RE.match(line)
    if m:
      msgs.append(Message(int(m.group(1)), m.group(2), int(m.group(3))))
      continue
    s = SG_RE.match(line)
    if s and msgs:
      msgs[-1].signals.append(Signal(
        s.group(1), int(s.group(2)), int(s.group(3)),
        s.group(4) == '0',            # @0 = big-endian (Motorola), @1 = little (Intel)
        s.group(5) == '-',
        float(s.group(6)), float(s.group(7))))
  return msgs


def bits_available(sig: Signal, dlc: int) -> int:
  """How many bits actually exist from this signal's start bit to the end of the frame.

  Big-endian start bits are numbered MSB-first within each byte, so a signal starting at
  bit `s` has (s%8 + 1) bits left in its own byte plus every byte after it. Little-endian
  is the simple linear case. Getting this wrong is how a signal silently reads garbage.
  """
  if sig.big_endian:
    return (sig.start % 8 + 1) + 8 * (dlc - sig.start // 8 - 1)
  return dlc * 8 - sig.start


# opendbc's get_checksum_state() (opendbc/can/dbc.py) dispatches purely on the DBC FILENAME
# prefix. If the name matches one of these, signals named COUNTER and CHECKSUM in that DBC are
# validated with the brand's algorithm -- and a message that fails is held invalid, taking
# carState.canValid down with it. Naming a new DBC `toyota_*` opts you into Toyota's checksum
# whether you meant to or not.
CHECKSUM_PREFIXES = ("honda_", "acura_", "toyota_", "lexus_", "hyundai_canfd_generated",
                     "vw_meb_2024", "vw_mqb", "vw_mqbevo", "vw_meb", "vw_mlb", "vw_pq",
                     "subaru_global_", "chrysler_", "fca_giorgio", "comma_body",
                     "tesla_model3_party", "psa_")


def validated_brand(dbc_name: str) -> str | None:
  for pre in CHECKSUM_PREFIXES:
    if dbc_name.startswith(pre):
      return pre
  return None


def check(msgs: list[Message], dbc_name: str = "") -> list[str]:
  errs: list[str] = []
  by_addr: dict[int, list[str]] = defaultdict(list)
  by_name: dict[str, list[int]] = defaultdict(list)

  for m in msgs:
    by_addr[m.address].append(m.name)
    by_name[m.name].append(m.address)

    if not 0 < m.dlc <= 64:
      errs.append(f"{m.name} (0x{m.address:X}): implausible DLC {m.dlc}")

    seen: set[str] = set()
    for sig in m.signals:
      if sig.name in seen:
        errs.append(f"{m.name}.{sig.name}: duplicate signal name in message")
      seen.add(sig.name)

      avail = bits_available(sig, m.dlc)
      if sig.length > avail:
        errs.append(f"{m.name}.{sig.name}: {sig.length} bits from start {sig.start} "
                    f"overruns a {m.dlc}-byte frame (only {avail} bits available)")
      if sig.length <= 0:
        errs.append(f"{m.name}.{sig.name}: zero/negative length")
      if sig.factor == 0:
        errs.append(f"{m.name}.{sig.name}: factor 0 makes every value {sig.offset}")

      # The trap that cost a whole debugging session: opendbc keys counter/checksum
      # validation purely on the signal NAME -- but only when the DBC filename matches a
      # brand prefix. A field named COUNTER that isn't a rolling counter fails
      # MAX_BAD_COUNTER and holds the entire message invalid -> carState.canValid false
      # for the whole drive.
      if sig.name in ("COUNTER", "CHECKSUM"):
        brand = validated_brand(dbc_name)
        if brand:
          errs.append(f"{m.name}.{sig.name}: WARNING -- this DBC is named {brand!r}*, so opendbc "
                      f"validates {sig.name} with that brand's algorithm. If {m.name} is not "
                      f"really carrying one, the message is held INVALID every frame. Rename the "
                      f"signal unless you have confirmed it validates.")
        else:
          errs.append(f"{m.name}.{sig.name}: note -- inert, this DBC name matches no brand "
                      f"prefix in get_checksum_state(), so {sig.name} is treated as a plain "
                      f"field. Renaming the DBC to a brand prefix would silently activate it.")

  for addr, names in by_addr.items():
    if len(names) > 1:
      errs.append(f"address 0x{addr:X} defined {len(names)} times: {names}")
  for name, addrs in by_name.items():
    if len(addrs) > 1:
      errs.append(f"message name {name} defined {len(addrs)} times: {[hex(a) for a in addrs]}")
  return errs


def cross_reference(msgs: list[Message], carstate_src: str) -> list[str]:
  """Every cp.vl['MSG']['SIG'] in a carstate must exist in the DBC."""
  index = {m.name: {s.name for s in m.signals} for m in msgs}
  errs = []
  for msg_name, sig_name in sorted(set(VL_RE.findall(carstate_src))):
    if msg_name not in index:
      errs.append(f"carstate reads message {msg_name!r}, not in the DBC")
    elif sig_name not in index[msg_name]:
      errs.append(f"carstate reads {msg_name}.{sig_name}, not in the DBC "
                  f"(has: {sorted(index[msg_name])})")
  return errs


def _self_check() -> None:
  dbc = """
BO_ 1568 BODY_CONTROL_STATE: 8 XXX
 SG_ KEY_ON : 37|1@0+ (1,0) [0|1] "" XXX
 SG_ OVERRUN : 7|72@0+ (1,0) [0|1] "" XXX
 SG_ COUNTER : 31|8@0+ (1,0) [0|255] "" XXX
BO_ 36 SPEED: 8 XXX
 SG_ ENCODER : 0|16@1+ (1,0) [0|65535] "" XXX
BO_ 36 DUPE: 8 XXX
"""
  msgs = parse_dbc(dbc)
  assert len(msgs) == 3, msgs
  bcs = msgs[0]
  assert bcs.address == 1568 and bcs.dlc == 8 and len(bcs.signals) == 3

  # KEY_ON: big-endian start 37 -> byte 4, bit 5. 6 bits left in its byte + 3 whole bytes.
  key_on = bcs.signals[0]
  assert key_on.big_endian and bits_available(key_on, 8) == (37 % 8 + 1) + 8 * (8 - 4 - 1) == 30

  errs = check(msgs, 'toyota_fake_pt')
  joined = "\n".join(errs)
  assert "OVERRUN" in joined and "overruns" in joined, joined
  assert "COUNTER" in joined, joined
  assert "0x24 defined 2 times" in joined, joined
  # a valid signal must not be flagged
  assert "ENCODER" not in joined, joined

  xerrs = cross_reference(msgs, "x = cp.vl['SPEED']['ENCODER']\ny = cp.vl['SPEED']['NOPE']\n")
  assert len(xerrs) == 1 and "SPEED.NOPE" in xerrs[0], xerrs
  print("dbc_check self-check OK")


def main() -> int:
  p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  p.add_argument("dbc", nargs="?", help="path to the .dbc")
  p.add_argument("--carstate", help="carstate.py to cross-reference against the DBC")
  p.add_argument("--self-check", action="store_true")
  a = p.parse_args()

  if a.self_check:
    _self_check()
    return 0
  if not a.dbc:
    p.error("give a dbc path (or --self-check)")

  with open(a.dbc, encoding="utf-8") as f:
    msgs = parse_dbc(f.read())
  print(f"{a.dbc}: {len(msgs)} messages, {sum(len(m.signals) for m in msgs)} signals")

  import os
  dbc_name = os.path.splitext(os.path.basename(a.dbc))[0]
  brand = validated_brand(dbc_name)
  print(f"checksum/counter validation: {'ACTIVE via ' + repr(brand) if brand else 'inactive (no brand prefix match)'}")
  errs = check(msgs, dbc_name)
  if a.carstate:
    with open(a.carstate, encoding="utf-8") as f:
      errs += cross_reference(msgs, f.read())

  for e in errs:
    print("  " + e)
  hard = [e for e in errs if "WARNING" not in e]
  print(f"\n{len(hard)} problems, {len(errs) - len(hard)} warnings")
  return 1 if hard else 0


if __name__ == "__main__":
  sys.exit(main())
