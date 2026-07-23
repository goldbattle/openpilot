---
name: dev-loop
description: The end-to-end iteration workflow for this comma 4 recorder fork — where a change gets written, verified on the host in WSL, committed, deployed to the device over SSH, and confirmed live. The orchestration layer over the other skills; start here when the question is "how do I make and ship a change" or "what's the loop". Triggers on "how do we iterate", "the workflow", "make a change and deploy it", "what's the dev loop", "ship this to the car", "how do I test then deploy".
---

# dev-loop

The connective tissue over the other skills. This one answers "what order do I do things in";
the others do the actual work.

| Phase | Skill | What it owns |
|---|---|---|
| build/run/test on the host | `local-dev` | WSL setup, scons, pytest, running the UI locally |
| analyse recorded data | `route-replay` | replay, cabana, `rlog_stats.py` |
| add/debug a car | `car-port-passive` | DBC, brand package, panda ignition |
| deploy + inspect the device | `comma-device` | `comma.py` deploy/restart/status/logs/py |
| is this change in scope | `fork-scope` | the four-bucket rule, drift measurement |

## The loop

```
edit  ->  verify in WSL  ->  commit  ->  push  ->  deploy over SSH  ->  confirm live
```

1. **Edit** on the host (Windows). Match surrounding style; check `fork-scope` before adding a
   param, a manager predicate, or touching an upstream file.
2. **Verify in WSL** — this is the step that catches the expensive bugs. At minimum:
   `python -m py_compile` the changed files, and for anything under `selfdrive/ui/`,
   **`python -c "import openpilot.selfdrive.ui.ui"`**. Windows can't import openpilot, so a bare
   `py_compile` will not catch a broken import — and a broken UI import crash-loops the manager
   and leaves the device on the boot logo. That exact bug shipped once (a deleted module still
   imported by the big-UI path) because it was only compile-checked, not imported. For UI
   behaviour, run it against a replayed route (`local-dev` → run_ui.sh). For car/DBC changes,
   replay a pulled rlog through the parser (`route-replay`).
3. **Commit** — one logical change per commit, real message. Run any `demo()`/`_self_check()` in
   files you touched first.
4. **Push** to `origin/test` (both this repo and, for DBC/ignition, `goldbattle/opendbc`).
5. **Deploy over SSH**, never scp code: `comma.py deploy --branch test --restart` (add `--build`
   only if C++/capnp changed). It refuses to deploy an unpushed HEAD, stops before restart if
   scons fails, and polls until the ui is back.
6. **Confirm live** — `comma.py status` + `comma.py procs` (no uploader/athenad), then the
   feature. For anything touching CAN/ignition/recording, do it **engine-running**, not key-on:
   key-on sits at ~11.8 V and the device shuts off mid-test.

## Fast path vs slow path

- **UI and rendering** iterate fastest **in WSL**, not on the device. The mici layout runs on
  the host (`big_ui()` is false on a PC), pyray is pure Python (edit and rerun, no build), and a
  replayed route drives the whole stack. Reserve the device for "does it look right on the real
  panel" and for anything hardware-gated. A device round trip is ~40s of restart minimum; a WSL
  rerun is seconds.
- **Car/DBC/decode** iterate fastest by **replaying a pulled rlog through the real parser** — you
  can measure a `canValid` fix to a tenth of a percent without a second drive. Only go to the
  car to capture *new* CAN.
- **C++, panda firmware, real sensors, SMB, power** genuinely need the device (and firmware needs
  a `--build`). No shortcut.

## What only the device can tell you

Real camera exposure, touch on the physical panel, panda/CAN/ignition/OBD multiplexing, loggerd
writing segments, SMB upload against the NAS, power management and shutdown, driver monitoring
against a real face. Everything else has a host-side check — reach for it before deploying.

## When the device won't boot into a UI

SSH is independent of the UI, so a dead screen is never a dead end:

```bash
comma.py status           # what commit is actually checked out
comma.py logs -n 400      # why the manager is crash-looping
comma.py deploy --branch test --restart   # roll forward to a fixed commit
```

The manager crash-loops on a Python exception in an import path or in `process_config.py`. The
usual causes: a broken import (verify with `import ui` in WSL first), a CRLF shebang from an
scp'd shell script, or a syntax error in a process module. Roll forward or back over git; the
tree edits you scp'd for a quick test will block the next `git reset` — `--force` discards them.

## Golden rules, learned the hard way

- **`import ui` in WSL before every UI deploy.** py_compile is not enough.
- **Deploy over git, never scp code.** CRLF shebangs brick the boot; committed blobs are LF.
- **Never `pkill` to reload.** The manager forks from preimported state (stale bytecode) and
  only restarts `restart_if_crash=True` procs. `comma.py restart` is the only reload.
- **Test engine-running**, not key-on, for anything CAN/ignition/recording.
- **Check `fork-scope` before adding.** The whole fork is meant to be four buckets; keep it there.

## Related

`local-dev`, `route-replay`, `car-port-passive`, `comma-device`, `fork-scope` — this skill is
just the order to use them in.
