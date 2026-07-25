---
name: reverse-engineer-can
description: >
  Guide the full reverse-engineering loop for adding or refining signals in an
  opendbc DBC from passively-sniffed CAN logs. Covers getting a CSV out of a
  comma route (cabana or headless), the time-synced narrated-calibration
  technique that turns "which bit is the trunk" into a lookup, the inspection
  scripts (inventory / transitions / window / correlate / decode_check), and the
  confidence-tagged DBC comment style. Use when the user wants to decode an
  unknown CAN message, identify a signal, add a car's DBC, confirm a candidate
  bit, or says "reverse engineer", "decode this route", "what is this CAN
  signal", "add DBC", or "/reverse-engineer-can".
---

# Reverse-engineering a car's CAN bus into a DBC

You have passive CAN logs (no active probing) and want to name the bits. The one
technique that makes this tractable: **record narrated audio while you operate the
car, time-synced to the CAN capture.** "Now I'm opening the trunk ... now closing
it" at a known second turns bit-hunting into: window the log on that second, see
which byte is uniquely active, done. Everything below is in service of that loop.

Work in a checkout with the repo venv active (`import opendbc` must work). Base
new DBC work on the **`test`** branch, not `master` — it carries the working car
port on top of the DBC. Scripts live in `scripts/` next to this file.

## The loop

### 1. Get a CSV: `time,addr,bus,data`

Two ways, same columns:

- **Cabana (GUI).** From the openpilot repo: `tools/cabana/cabana <route>` (or
  `--demo`, or `--data_dir <dir> <route>` for a local route). Load your DBC with
  `--dbc path/to/car.dbc`. Export with *File > Save As... > CSV*. Cabana is also
  where you interactively edit signals and see them decode live — keep it open.
- **Headless, from a route folder.** When someone shares a route directory (rlog
  segments), no display needed:
  ```
  python3 scripts/extract_csv.py /path/to/route_dir out.csv
  ```
  Also takes a single `rlog.zst` or an openpilotci/comma URL. Output is identical
  to cabana's export and feeds every other script. Point it at the **parent route
  directory** to pool every segment — a single trailing segment (e.g. `--4`) can
  hold only a few seconds of CAN if the engine was already off, which will starve
  a slow-signal hunt.

### 2. Narrate and transcribe (the leverage step)

While driving/operating, record audio calling out **every** action and reading
gauges aloud: key positions, each door/trunk/hood, gear sweep P-R-N-D and any
sport gate, progressive brake and throttle, lights, wipers, climate, locks, plus
idle RPM / coolant needle / odometer / outside-temp readouts. A stationary
"driveway session" exercising switches you can't reach while driving is gold.

Transcribe with timestamps (Whisper works well; medium model, `--output_format
srt`). The SRT timestamps and the CSV `time` share a zero, so a narration line at
54 s points straight at the CAN frames at 54 s.

**The known-delta shortcut (for slow signals you can't toggle).** Fuel level,
odometer, oil life — things that don't respond to a button in real time — can't
be windowed against narration. Instead capture two routes bracketing a known
physical change (fill up 10 gal; drive 50 mi) and compare *distributions*: the
signal is the field whose value separates cleanly between the two routes by an
amount matching the change, while staying within-route stable. Pool all segments
of each route (§1), then for every byte and every 16-bit BE/LE field score
`(median_A - median_B) / (iqr_A + iqr_B)`; rank by that separation. Crucially,
this also gives a **clean negative**: if the top separation is just coolant or a
byte-boundary flip, the quantity is not on the bus. A 10 gal fill (≈60% of tank)
that fails to move any field this way is proof the value lives on an analog gauge
sender / request-only PID, not the broadcast bus — record that, don't re-hunt it.

### 3. Inventory — where do signals live?

```
python3 scripts/inventory.py out.csv [--bus 0]
```
One line per message ID: frame count, rate, and per-byte activity. `b3=const1c`
never moved (padding / ECU id); `b2:107v` took 107 values (a live signal or
counter). Aim the next steps at the busy bytes. Note which messages appear only
with the key on, or only when the engine runs — that timing is itself evidence.

### 4. Isolate — which byte did the action move?

Read the narrated start/end seconds of one action, then:
```
python3 scripts/window.py out.csv 142 200      # e.g. the hood test
```
A byte flagged `<-- UNIQUE` took values in that window seen nowhere else in the
log — that is your signal. Suppress always-noisy driving messages (wheels, RPM,
steering, counters) with `--ignore 0x20 0xb0 0xb2 ...`.

Then pin the exact bit and confirm the on/off edges line up with the narration:
```
python3 scripts/transitions.py out.csv 0x620 --from 140 --to 205
```
`^^` marks the bytes that changed each step; a physical toggle shows the same
bit flipping on each narrated actuation.

### 5. Confirm — is it really that quantity?

For a **numeric** signal, correlate it against something you trust:
```
# mean wheel speed should equal the SPEED signal
python3 scripts/correlate.py out.csv 0xb4:5:2 --vs 0xb0:1:2
# coolant should climb monotonically with time on a cold start
python3 scripts/correlate.py out.csv 0x3b4:2:1 --vs-time --from 447 --to 750
# is a frame crank-synchronous (engine/fuel timing)? check its period vs RPM
python3 scripts/correlate.py out.csv 0x2c4:0:2 --period-of 0x398
```
Spec is `addr:byte_offset:num_bytes` (append `:le` for little-endian). It is
byte-granular — enough for wheels, speed, RPM, temps, torque, fuel. For a
sub-byte field, promote the candidate into the DBC and decode it exactly:
```
python3 scripts/decode_check.py out.csv <dbc_name> DOOR_LOCKS          # every state change
python3 scripts/decode_check.py out.csv <dbc_name> BODY_CONTROL_STATE --at 54.5 206 525
```
`decode_check` runs the real `CANParser`, so it is the ground truth for whatever
you have written — including sub-byte bits `window.py` can't see.

### 6. Write it down — signal + a comment that earns trust

Edit the DBC (`opendbc/dbc/<car>.dbc`). Every `SG_` you are not 100% sure of gets
a `CM_` comment stating **how you know and how confident you are**, tagged with a
source. The Camry XV40 DBC is the reference for the style; reuse its tag vocabulary:

- `audio calibration test` — matched against the narrated recording. Highest confidence.
- `device IMU correlation` — correlated against the comma device's own gyro/accel.
- `vehicle geometry` — derived from Ackermann/wheelbase cross-checks.
- `opendbc production` / `factory ref` / `iQ dbc` — carried from a known DBC.
- `byte-stat analysis` — statistics over this car's own logs, no external reference.
- `web source` / `colostate.edu` — public write-up; name it.

State the evidence and the counter-evidence. "Confirmed by a 3-press/3-release
narrated test" is a claim a reader can trust; "brake pressure" alone is not.

### 7. Commit on `test`

Small, single-topic commits with the evidence in the message. Validate the DBC
parses before committing:
```
python3 -c "from opendbc.can.parser import CANParser; CANParser('<dbc_name>', [], 0)"
```

## Discipline that keeps the DBC honest

- **Confidence is part of the data.** Distinguish confirmed / correlated /
  carried-from-production / guessed. Say which, in the comment.
- **Record negative results.** "Turn signals produce nothing on this bus,
  verified with a dedicated stationary test" is a real finding — it stops the
  next person re-hunting. Absence needs a deliberate test, not just "didn't see it".
- **Reject implausible scales.** An idle of 340 rpm or a coolant plateau of
  131 °C means the scale is wrong. Sanity-check against physics.
- **Beware copies and counters.** Bytes that move together may be duplicates; a
  byte cycling the full 0-255 every frame is a counter/checksum, not a signal.
- **Iterate.** Each new recording (different conditions, a cold start, a fob) both
  adds signals and lets you upgrade earlier guesses to confirmations — or refute
  them. Re-run the loop as data arrives.

## Script reference

| Script | Does |
|---|---|
| `extract_csv.py` | route folder / rlog / URL → cabana-format CSV, headless |
| `inventory.py` | per-message frame count, rate, per-byte variability |
| `transitions.py` | every payload change with the changed bytes marked |
| `window.py` | byte uniquely active during a narrated time window |
| `correlate.py` | Pearson r vs another signal / time / message period (crank-sync) |
| `decode_check.py` | replay through the real CANParser; ground-truth a candidate |
| `canlog.py` | shared CSV loader imported by the rest (run it for a self-check) |
