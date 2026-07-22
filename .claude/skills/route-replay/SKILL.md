---
name: route-replay
description: Replay and analyse a recorded openpilot route on the host — offline replay to drive the UI and the whole stack, cabana for CAN and DBC work, panda-jungle CAN bus replay into real hardware, and scripted rlog analysis for canValid, ignition, message rates and CAN addresses. Use when the user wants to look at recorded data, check whether a drive recorded correctly, debug a decode against a real log, develop the UI without a car, or stream live CAN off the device. Triggers on "replay that route", "open cabana", "check the rlog", "did the drive record", "look at the CAN data", "run the UI locally".
---

# route-replay

Analysing recorded data **on the host laptop**. Nothing here touches the device except pulling
routes off it and (optionally) streaming live CAN from it.

Four different things get called "replay". Pick the right one:

| You want to | Use | Where |
|---|---|---|
| Drive the UI/stack from logged messages | `tools/replay/replay` | host (WSL) |
| Look at CAN, author or check a DBC | `tools/cabana/cabana` | host (WSL) |
| Push logged CAN onto real hardware | `tools/replay/can_replay.py` + panda jungle | host + jungle |
| Answer a question about a log in bulk | `scripts/rlog_stats.py` (here) | host or device |

All of these need openpilot importable and built — **WSL, not Windows**. See `local-dev`.

## Getting a route onto the host

```bash
python .claude/skills/comma-device/scripts/comma.py routes
python .claude/skills/comma-device/scripts/comma.py pull 00000024--018c2cdbb5 ./routes
```

Segments land as `./routes/00000024--018c2cdbb5--0`, `--1`, … Each holds `rlog.zst` (full rate)
plus `fcamera/dcamera/ecamera.hevc`. `qlog` is heavily decimated — for CAN work always use
`rlog`. A segment still being written holds a `.lock` and is incomplete.

## 1. Offline replay — the whole stack, no car

Publishes every logged message plus VisionIPC camera frames, so the real UI and any daemon runs
against it.

```bash
# terminal 1
openpilot/tools/replay/replay "00000024--018c2cdbb5" --data_dir ./routes
# terminal 2
./openpilot/selfdrive/ui/ui.py
```

**You get the mici UI locally.** `gui_app.big_ui()` is `HARDWARE.get_device_type() in
('tici','tizi') or BIG_UI`, and a PC reports `"pc"` — so `ui.py` instantiates
`MiciMainLayout` at 536×240, the same layout the comma 4 runs. Set `BIG=1` to get the large
`MainLayout` instead. This means onroad-view work — model rendering, the driver page, the
status line, swipe behaviour — is genuinely developable on the host.

`--demo` uses comma's public demo route with no auth. A remote route from your own comma account
needs `python3 openpilot/tools/lib/auth.py` first. `ZMQ=1` switches transport from msgq to zmq.

The UI is pure Python (pyray/raylib): edit and rerun, no rebuild. Only C++
(camerad/loggerd/encoderd) needs `scons`.

## 2. Cabana — CAN and DBC work

```bash
openpilot/tools/cabana/cabana "00000024--018c2cdbb5" --data_dir ./routes --dbc <your.dbc>
openpilot/tools/cabana/cabana --demo
```

Useful flags: `--no-vipc` (skip video, much faster to open), `--ecam` (wide camera), `--dbc` to
load a work-in-progress DBC.

**Live from the device** — start the bridge on the device, point cabana at it:

```bash
python .claude/skills/comma-device/scripts/comma.py exec "cd /data/openpilot && ./openpilot/cereal/messaging/bridge &"
openpilot/tools/cabana/cabana --zmq <device ip>
```

While streaming, cabana logs to `~/cabana_live_stream/`, replayable later from the stream
selector. This is the fastest way to watch a signal change while you physically move something
in the car — open a door, turn the key, press the brake.

Cabana can also read a panda directly: `--panda`, `--panda-serial <serial>`, `--socketcan <dev>`.

## 3. CAN bus replay — logged CAN onto real hardware

`openpilot/tools/replay/can_replay.py` transmits a route's CAN out of a **panda jungle** (not a
normal panda) so a device under test sees a car that isn't there.

```bash
python openpilot/tools/replay/can_replay.py --route <route>
FLASH=1 python openpilot/tools/replay/can_replay.py      # reflash the jungle first
ON=5 OFF=5 python openpilot/tools/replay/can_replay.py   # cycle ignition 5s on / 5s off
PWR_ON=5 PWR_OFF=5 ...                                   # cycle panda power
```

It only sends buses 0–2. Cycling ignition is how you exercise the onroad/offroad transition —
including CAN-based ignition — on a bench instead of in the car.

## 4. Scripted analysis — the everyday one

```bash
S=.claude/skills/route-replay/scripts/rlog_stats.py

python $S ./routes/00000024--018c2cdbb5     # services, rates, gaps, missing services
python $S <route> --canvalid --ignition     # did the decode and the key work
python $S <route> --can-addrs               # unique addresses per bus
python $S <route> --field carState.vEgo --field carState.gearShifter
```

`--canvalid` is the one that matters after a DBC change: percentage plus the timestamp of the
first false. Below ~95% the cause is almost always the DBC — a signal named `COUNTER` or
`CHECKSUM` that isn't one gets brand-validated and holds its whole message invalid. See
`car-port-passive`.

Missing-service diagnosis is built in: no `carState` means `card` never loaded the platform, no
`modelV2` means modeld isn't running, no `driverStateV2` means the driver camera isn't being
logged (check `RecordFront`), and so on.

For anything ad-hoc, `LogReader` is a plain iterator — usually five lines:

```python
from openpilot.tools.lib.logreader import LogReader
for msg in LogReader("./routes/00000024--018c2cdbb5"):
    if msg.which() == "carState":
        print(msg.carState.vEgo, msg.carState.canValid)
```

## Replaying a decode change without a car

The highest-value trick: replay recorded CAN through the *real* parser to measure a DBC fix
before going back to the car.

```python
from opendbc.can.parser import CANParser
from opendbc.car import Bus
from opendbc.car.toyota_xv40.values import CAR, CanBus, DBC
from openpilot.selfdrive.pandad import can_capnp_to_list
from openpilot.tools.lib.logreader import LogReader

cp = CANParser(DBC[CAR.TOYOTA_CAMRY_XV40_2010][Bus.pt], [("BODY_CONTROL_STATE", 0)], CanBus.main)
valid = total = 0
for msg in LogReader("./routes/<route>"):
    if msg.which() != "can":
        continue
    cp.update(can_capnp_to_list([msg.as_builder().to_bytes()]))
    total += 1
    valid += bool(cp.can_valid)
print(f"can_valid {valid}/{total} ({100*valid/total:.1f}%)")
```

This is how the Camry `COUNTER` fix was measured: 6.6% → 99.8% on the same recorded drive, no
second drive needed.

> **`can_valid` debounces over property *reads*, not updates.** It needs `CAN_INVALID_CNT` (5)
> consecutive reads to flip. Poll it every loop iteration, as above — reading it once at the end
> silently reports a stale value and makes a broken parse look fine.

## Gotchas

- **`xattr_cache.getxattr` caches per process and never invalidates.** Sampling a file's
  upload-done state twice in one script returns the same stale answer. Clear
  `xattr_cache._cached_attributes` between samples.
- **A route missing its tail is normal while recording.** Replay of a live route ends early —
  that's the `.lock` segment, not corruption.
- **Route numbers are hex.** `#24` in the UI is `00000018--…`.

## Related

`local-dev` for getting the host able to run any of this, `comma-device` to pull routes,
`car-port-passive` for what to do with what you find.
