---
name: car-port-passive
description: Add a passive (dashcamOnly) car port to opendbc so a comma device can log and decode a car's CAN and auto-record drives on ignition — new brand package, DBC authoring and validation, pinned fingerprint, and CAN-based ignition in panda firmware. Use when adding or debugging support for an unsupported car, writing or fixing a DBC, working out why carState.canValid is false, why CAN frames are missing, or why the device never goes onroad. Triggers on "add this car", "port my car", "decode this CAN", "write a DBC", "canValid is false", "no CAN frames", "device won't go onroad".
---

# car-port-passive

How to make an unsupported car observable: openpilot logs and decodes its CAN, publishes a real
`carState`, and starts recording when you turn the key — while never sending a single frame to
the car. This is the `dashcamOnly` path, not a control port.

Worked example in this repo: the 2010 Camry (XV40), `opendbc_repo/opendbc/car/toyota_xv40/`.

## The five pieces

A passive port is exactly these. If a drive isn't recording, one of them is missing.

| # | Piece | Where |
|---|---|---|
| 1 | CAN physically reaches the panda | wiring + OBD multiplexing |
| 2 | A DBC that decodes it | `opendbc/dbc/<name>.dbc` |
| 3 | A brand package | `opendbc/car/<brand>/` |
| 4 | The platform is selected | pinned `FINGERPRINT` in `launch_env.sh` |
| 5 | Ignition is detected | `opendbc/safety/ignition.h` (**panda firmware**) |

## 1. Get CAN in the first place

With a real comma harness, the powertrain bus is on **panda bus 0** and nothing special is
needed. Through the **OBD-II port** it is on **bus 1**, and bus 1 is only connected when OBD
multiplexing is on — `set_safety_model(ELM327, 0)`, param **0** = mux ON, param 1 = mux OFF.

Confirm before writing any DBC:

```bash
python .claude/skills/comma-device/scripts/comma.py can -s 5
```

Frames but 0 addresses on the bus you expect → multiplexing. Nothing at all → power/ignition.

> **The mux trap.** `PandaSafety::updateMultiplexingMode()` runs on the offroad→onroad edge and
> sets ELM327 with param **1**. If your code only compares the safety *mode* when re-asserting
> the mux, that param 1 sticks — and if ignition comes from CAN on bus 1, going onroad kills the
> very frames that asserted ignition, dropping you back offroad with the mux stuck off. Compare
> **mode and param**. Normal cars never hit this: their ignition is on bus 0, they have
> `ignitionLine` from the harness as an independent floor, and fingerprinting drives the mux
> back on its own.

## 2. Author and validate the DBC

Survey first, guess never:

```bash
# in WSL or on the device -- needs openpilot importable
python .claude/skills/car-port-passive/scripts/can_survey.py <route> --bus 1
python .claude/skills/car-port-passive/scripts/can_survey.py <route> --addr 0x620
python .claude/skills/car-port-passive/scripts/can_survey.py <route> --addr 0x620 --watch KEY_ON:4:5
```

The per-byte report tells you what each byte is: constant, enum/bitfield (booleans), smooth
analog (speed, angle), or a genuine rolling counter.

Then validate statically — this runs anywhere, including Windows:

```bash
python .claude/skills/car-port-passive/scripts/dbc_check.py <dbc> --carstate <carstate.py>
```

It catches: signals overrunning the frame, duplicate addresses/names, zero factors, every
`cp.vl['MSG']['SIG']` in the carstate that doesn't exist in the DBC, and the two name traps
below.

### Never name a signal `COUNTER` or `CHECKSUM` unless it really is one

`opendbc/can/dbc.py:set_signal_type()` keys on the signal **name** — but only when the DBC
**filename** matches a brand prefix in `get_checksum_state()` (`toyota_`, `honda_`, `vw_mqb`,
…). Name your file `toyota_camry_xv40_2010_pt.dbc` and you have opted into Toyota's counter and
checksum validation whether you meant to or not.

A field named `COUNTER` that doesn't increment by 1 every frame fails `MAX_BAD_COUNTER=5` and
the message is held **invalid from then on** — which is what drags `carState.canValid` to ~7%
for an entire drive. It shows up as a UI/logging problem and is actually a naming problem.

`CHECKSUM` fails differently and more quietly: there is no failure counter, the frame simply
does not update (`parser.py`: "must have good counter and checksum to update data"), so the
message goes stale and eventually times out. The tell is decoded values frozen at their
initial 0 while the address is clearly present on the bus.

> **Verified for this repo's DBC 2026-07-22:** `toyota_camry_xv40_2010_pt` declares `CHECKSUM`
> on `SPEED` and `STEER_ANGLE_SENSOR`, and because of the `toyota_` prefix both are validated
> with `toyota_checksum`. They **pass** — replaying route `00000024--018c2cdbb5--0` gives
> `can_valid 6089/6100 (99.8%)`, `counter_fail 0` on all three subscribed messages, `SPEED` at
> 51.0 Hz and `STEER_ANGLE_SENSOR` at 89.1 Hz with live values (`STEER_ANGLE -3.0`,
> `STEER_SENSOR_QUALITY 100`). The 2010 Camry uses the same checksum as modern Toyotas. No
> action needed; `dbc_check.py` still warns because the warning is right in general.

Big-endian bit math, since it's the other silent-garbage source:
`available = (start % 8 + 1) + 8 * (dlc - start // 8 - 1)`.

## 3. Make it its own brand, usually

Prefer `opendbc/car/<newbrand>/` over a flag inside an existing brand when the car doesn't share
the brand's core messages. The XV40 has no `EPS_STATUS`, `STEER_TORQUE_SENSOR` or `PCM_CRUISE`,
which is most of what `toyota/carstate.py` reads unconditionally. (Subaru's `PREGLOBAL` flag
works only because preglobal *does* share most messages.) A separate brand also means upstream
changes to that brand never conflict on rebase.

Four files: `values.py`, `interface.py`, `carstate.py`, `carcontroller.py` (a stub).

```python
# values.py -- the bus the car is actually observed on
class CanBus:
  main = 1     # OBD-II multiplexed onto bus 1; a harnessed car would be 0

class CAR(Platforms):
  MY_CAR = MyPlatformConfig([], CarSpecs(mass=..., wheelbase=..., steerRatio=...))
  #        ^ empty CarDocs list: never controllable, must not appear in the supported car list
```

```python
# interface.py
ret.dashcamOnly = True          # card sets CP.passive, skips controls_update() entirely
ret.safetyConfigs = [get_safety_config(SafetyModel.noOutput)]
ret.radarUnavailable = True
# lateralTuning is unused, but test_car_interfaces asserts non-empty gain lists for every
# registered platform. Zeroed placeholders, same as mock.
ret.lateralTuning.pid.kpBP = [0.]; ret.lateralTuning.pid.kpV = [0.]
ret.lateralTuning.pid.kiBP = [0.]; ret.lateralTuning.pid.kiV = [0.]
```

`dashcamOnly` is the supported "observe but never control" mode (precedent: `psa`, `mock`).
`card.py` calls `state_publish()` unconditionally and gates only `controls_update()`, so
carState publishes every cycle and nothing is ever written to `sendcan`.

Gotchas that bite: `CarState.gas` and `engineRpm` are **deprecated** in `car.capnp` (only
`gasPressed` survives — they moved under `deprecated`). Derive `vEgo` from a speed message
rather than `parse_wheel_speeds` if any wheel sensor reads 0 at low speed; on the XV40 the
front-right reads exactly 0 below ~6 km/h and dragged the four-wheel mean down 25%.

## 4. Skip fingerprinting, don't author it

An old car has no FW/VIN query to answer and no addresses that match a supported platform. Don't
write FINGERPRINTS/FW_VERSIONS tables. Pin it in `launch_env.sh`:

```bash
if [ -z "$FINGERPRINT" ]; then
  export FINGERPRINT="TOYOTA_CAMRY_XV40_2010"
  export SKIP_FW_QUERY="1"
fi
```

`car_helpers.fingerprint()` honours both. The `-z` guard keeps `scripts/launch_corolla.sh`
working. `SKIP_FW_QUERY` also avoids the ECU probe, which would drive the OBD multiplexer.

## 5. Ignition is panda FIRMWARE, not Python

**This is the one that wastes the most time.** `ignition_can` is set in
`opendbc/safety/ignition.h` (`ignition_can_hook`), called from panda `board/drivers/fdcan.h` in
the CAN RX ISR. Writing Python in `opendbc/car/<brand>/` or adding a DBC signal does **nothing**
for ignition. There are only a handful of cases upstream (GM 0x1F1, Rivian 0x152, Tesla 0x221,
Mazda 0x9E, VW MEB 0x3C0) and they're all hardcoded bit math.

All upstream cases sit inside an `if (msg->bus == 0U)` block. An OBD-observed car is on bus 1, so
write your case **outside** that block:

```c
if ((msg->bus == 1U) && (msg->addr == 0x620U) && (GET_LEN(msg) == 8)) {
  // BODY_CONTROL_STATE->KEY_ON: set in RUN/START, clear in ACCESSORY and OFF.
  // Start bit 37, big-endian, 1 bit -> byte 4 bit 5.
  ignition_can = ((msg->data[4] >> 5U) & 1U) != 0U;
  ignition_can_cnt = 0U;
}
```

Read-only, like every case above it. The 2 s no-CAN timeout in `main.c` applies unchanged, so
you do **not** need to invent a timeout.

You can rebuild panda from this tree — it has no submodules of its own; `panda/SConscript` does
`import opendbc` and builds against `opendbc.INCLUDE_PATH`. `pandad.py` auto-flashes on a
signature mismatch, with bootstub/DFU recovery. So the loop is just: edit `ignition.h`, deploy,
restart. Add a libsafety test next to `opendbc/safety/tests/test_mazda.py`.

**Do not** work around ignition with `STARTED=1` (pandad's spoof) — it forces the device onroad
and starts the whole self-driving stack.

## Verifying a port end to end

1. `comma.py can -s 5` — frames on the expected bus
2. `comma.py status` — `ignitionCan` flips true when you turn the key
3. `started` goes true; `card`, `loggerd`, `camerad`, `modeld` come up (`comma.py procs`)
4. `canValid` true and holding
5. `comma.py routes` — a new route appears, flagged RECORDING, with no button press
6. `comma.py pull <route>` and check offline — see the `route-replay` skill

Sanity-check decoded values against the real world: gear in park, doors closed, parking brake
off, seatbelt latched. A `canValid` of true with nonsense values means the bit layout is wrong,
not the wiring.

**Test with the engine running.** Key-on-only sits at ~11.8 V, exactly the low-voltage
shutdown threshold, and the device will power off mid-test.

## Related

`comma-device` to deploy and inspect, `route-replay` to analyse the data, `fork-scope` for
whether a change belongs in this fork at all.
