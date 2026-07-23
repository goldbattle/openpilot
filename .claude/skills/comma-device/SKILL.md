---
name: comma-device
description: Deploy code to a comma device (comma 3/3X/4) over SSH, restart it, and inspect live state — branch/commit, ignition, panda health, CAN, running processes, recorded routes, logs. Use when the user wants to push or deploy to the device, update or restart it, check whether it is onroad, debug why a change did not take effect, tail device logs, or pull a recorded route off the device. Triggers on "deploy to the comma", "update the device", "restart openpilot", "is it onroad", "check the panda", "what's on the device", "pull that route".
---

# comma-device

Drive the device from the dev box. Everything goes through `scripts/comma.py`, which shells out
to `ssh`/`scp` and never imports openpilot (it can't — see `openpilot-windows-dev-limits`).

## Setup

```bash
export COMMA_IP=<device ip>          # DHCP, changes on reboot — never hardcode it in a file
export COMMA_SSH_KEY=~/.ssh/id_ed25519   # optional; omit to use the ssh default
```

Auth is **publickey only**, user `comma`. The device pulls authorized keys from a GitHub
account, so the key must be on that account. `comma`/`comma` is the *serial console* password
and is not an SSH path. USB-C to a PC is not an SSH path either — unpowered it drops to
Qualcomm EDL mode. LAN only.

Find the IP if it moved: check the router, or `ssh comma-<dongleid>` via the comma proxy if the
device has prime.

## The commands

```bash
P=.claude/skills/comma-device/scripts/comma.py

python $P status              # branch@commit, dirty?, disk, ignition, panda health, canValid
python $P procs               # managed procs + asserts uploader/athenad are absent
python $P can -s 5            # sample live CAN: rate, per-bus counts, top addresses
python $P routes              # recorded routes, size, which is still recording
python $P logs -n 300 -g card # tail the manager tmux pane, optionally grepped
python $P params RecordFront  # read a param; add a value to write it
python $P pull 00000024--018c2cdbb5 ./routes
python $P py probe.py         # ship a local .py to the device and run it under the venv
python $P exec ls /data/media/0/realdata
```

**`py` is the one to reach for when investigating.** Write a normal local script that imports
openpilot freely (it runs *on the device*, where the imports work), and `py` scp's it to
`/tmp` and runs it with `PYTHONPATH` set. This replaces the scp-a-file-then-`exec`-python dance
— every "check something live on the device" probe should be a `py` script, not an inline
snippet fought through two layers of shell quoting. Anything after the path is passed as argv.

## Deploying

**Deploy over git, never scp.** The device is a real checkout of the fork.

```bash
git push origin HEAD:test
python $P deploy --branch test --build --restart
```

`deploy` refuses to run if local `HEAD != origin/<branch>` (otherwise you silently deploy the
previous commit) and refuses to reset over a dirty device tree unless you pass `--force`.
`--build` runs scons; if it fails, deploy stops **before** restarting rather than leaving you
with a device that boots broken. With `--restart` it then **polls until the ui process comes
back** (up to 90s) and tells you if it didn't — no more `sleep 45 && hope`. A ui that never
comes up means the manager is crash-looping; go straight to `logs`. `restart` waits the same
way (`--no-wait` to skip).

Why not scp: a shell script copied from a Windows working tree carries CRLF, the
`#!/usr/bin/env bash` shebang becomes `bash\r`, and the device bricks at the boot logo with
`/usr/bin/env: 'bash\r': No such file or directory`. Python tolerates CRLF; shell does not.
Committed blobs are LF, so the git path is always safe. (`core.autocrlf=true` here.)

For a one-off experiment scp is fine for **`.py` files only** — but a dirty tree then blocks
the next update, so revert with `git -C /data/openpilot checkout -- <path>` when done.

## Reloading: the rule that costs the most time

**Never `pkill` a process to reload it.** Two independent traps, both hit for real:

1. `PythonProcess.prepare()` imports the module **in the manager**, then forks the child. The
   re-import in the child is a no-op because the module is already in `sys.modules`. A
   respawned child runs **stale bytecode**.
2. `ensure_running()` only auto-restarts procs declared `restart_if_crash=True`. `ui` is; most
   (`dmonitoringd`, `card`, …) are **not**. Killing those leaves them dead until a full
   restart, which looks exactly like "my change broke it".

So: `python $P restart` (systemctl, ~40s) or `reboot`. That is the only reload.

C++/capnp changes additionally need `python $P build` first. `scons` and `capnpc` are not on a
non-login shell's PATH — the script exports `/usr/local/venv/bin` for you.

## Reading device state

`status` prints live `deviceState`/`pandaStates`/`carState`. What to look at when something is
off:

| symptom | check |
|---|---|
| not going onroad | `ignitionCan` / `ignitionLine`; on an OBD-only install `ignitionLine` is structurally always false |
| no CAN frames | `safetyModel` must be `elm327` **and** `safetyParam` 0 — param 1 means multiplexing is OFF |
| `canValid` false | the DBC, not the wiring: a signal literally named `COUNTER` gets counter-validated and fails the whole message |
| device keeps powering off | `voltage_mV` — below 11800 mV offroad for 60s triggers a shutdown. Engine running ≈13.6 V, key-on-only ≈11.5 V |
| segments stop appearing | disk; `deleter` rotates oldest-first below `max(5 GiB, 10%)` |

## Device layout

- `/data/openpilot` — the checkout that runs. Nested layout (`openpilot/selfdrive/...`), with
  `openpilot/{selfdrive,system,common}` symlinked to the top-level dirs.
- `/data/media/0/realdata` — recorded segments.
- `/data/safe_staging/{merged,finalized}` — the updater's OverlayFS staging, **not** what runs.
  A pending finalized update is swapped over `/data/openpilot` on next launch, which is how
  on-device edits get silently reverted.
- systemd `comma.service` → tmux session `comma` → `launch_chffrplus.sh` → `manager.py`.
- Python must be `/usr/local/venv/bin/python3` with `PYTHONPATH=/data/openpilot`.

## Testing a change end to end

1. `python $P deploy --branch test --build --restart`
2. `python $P procs` — every expected proc running, **no uploader/athenad**
3. `python $P status` — ignition and `canValid` as expected
4. Turn the key. `python $P can -s 5` should show frames; `status` should flip `started` true
5. `python $P routes` — a new route appears and is flagged RECORDING
6. `python $P pull <route>` and analyse offline — see the `route-replay` skill

**Test with the engine running, not just key-on.** Key-on-only sits at ~11.8 V, right at the
low-voltage shutdown threshold, and the device will power off mid-test.

## Troubleshooting

- **Change didn't take effect** — you pkill'd, or a pending OverlayFS update reverted the tree.
  `python $P status` shows the commit actually checked out.
- **Stuck on the boot logo** — manager is crash-looping. `python $P logs -n 400`. Classic causes:
  a CRLF shebang from an scp'd shell script, or a Python exception in `process_config.py`
  (every `ensure_running` call raises, so the UI never launches).
- **Panda in bootstub after a flash** — `pandad.py` asserts. Recover:
  `python $P exec "cd /data/openpilot && PYTHONPATH=. /usr/local/venv/bin/python3 -c \"from openpilot.common.hardware import HARDWARE; HARDWARE.recover_internal_panda()\""`
  then let pandad reflash. The comma 4's panda is internal over **SPI** (`/dev/spidev0.0`) — it
  will never show up in `lsusb`.
- **`ModuleNotFoundError: capnp`** — you used `/usr/bin/python3` instead of the venv one.

## Related

`dev-loop` for the full edit->deploy workflow this fits into, `route-replay` for analysing what you recorded, `car-port-passive` for adding a car,
`fork-scope` for what belongs in this fork at all.
