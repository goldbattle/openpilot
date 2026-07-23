---
name: local-dev
description: Build, run and test openpilot on the host laptop in WSL Ubuntu before touching the device — environment setup, scons, pytest, running the mici UI locally against a replayed route, and which things genuinely cannot be verified off-device. Use when the user wants to build or test locally, set up WSL, run the test suite, iterate on UI code without the car, or asks whether a change can be checked before deploying. Triggers on "build it locally", "run the tests", "set up WSL", "can we test this here", "before we deploy".
---

# local-dev

The host is a **Windows laptop that SSHs into the comma**. Two environments, and knowing which
one a task belongs in saves the most time:

- **Windows itself** — editing, git, and pure-Python static checks. openpilot **cannot be
  imported at all** here: `common/gpio.py` needs `fcntl` (POSIX only) and `common/params_pyx` is
  an unbuilt Cython extension.
- **WSL Ubuntu** — the real dev environment. Builds, tests, replay, cabana, the UI.
- **The device** — only for what needs the actual hardware.

## What can be tested where

| Work | Host (WSL) | Device |
|---|---|---|
| mici UI layouts, widgets, onroad view, model rendering | **yes**, via replay | confirm on real screen |
| DBC authoring, CANParser, `canValid` measurement | **yes**, against a pulled rlog | — |
| car interface / carstate logic, `pytest` | **yes** | — |
| panda safety (libsafety unit tests) | **yes** | — |
| C++ compile (camerad, loggerd, pandad) | **yes** (`scons`) | arm build, also needed there |
| real cameras, VisionIPC from camerad | no | yes |
| panda hardware, real CAN, OBD multiplexing, ignition | no (unless you have a jungle) | yes |
| loggerd writing segments, disk rotation | partly | yes |
| SMB upload against the NAS | no | yes |
| screen brightness, power management, shutdown | no | yes |
| driver monitoring against a real face | no | yes |

**The mici UI does run on the host.** `gui_app.big_ui()` is
`HARDWARE.get_device_type() in ('tici','tizi') or BIG_UI`; a PC reports `"pc"`, so `ui.py`
builds `MiciMainLayout` at 536×240 — the same layout the comma 4 runs. `BIG=1` forces the large
layout instead. So layout, swipe, rendering and model-overlay work belongs on the host; only
"does it look right on the actual panel" needs the device.

## Setting up WSL

Windows optional components must be enabled and **the machine rebooted** before any `wsl`
command works (`WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED` means the reboot hasn't happened):

```powershell
wsl --install --no-distribution     # elevated, then REBOOT
wsl --install -d Ubuntu-24.04       # prompts for a UNIX username/password
```

Ubuntu 24.04 ("noble") is the right choice — `tools/op.sh` `op_check_os()` accepts only
`focal|jammy|kinetic|noble`. `--no-launch` matters: without it the installer opens a console and
blocks on an interactive UNIX username/password prompt. Provision afterwards instead:

```powershell
wsl -d Ubuntu-24.04 --user root -- bash -lc "useradd -m -s /bin/bash -G sudo <user> && printf '[user]\ndefault=<user>\n[boot]\nsystemd=true\n' > /etc/wsl.conf"
wsl --terminate Ubuntu-24.04
```

**Two packages openpilot's setup does not install and needs** (both verified 2026-07-22):

- `git-lfs` — without it `git lfs pull` is silently skipped and the models stay pointer stubs.
  `tools/op.sh check` catches this. Install, then `git lfs install && git lfs pull`
  (~1.8 GB: `big_driving_supercombo.onnx` alone is 1.7 GB).
- `clang` — tinygrad compiles the model through it. Without it `scons` dies with
  `FileNotFoundError: 'clang'` on `driving_tinygrad.pkl.chunkmanifest`, *after* several minutes
  of model work, so it looks like a model problem and isn't.

**Clone into the WSL filesystem, not `/mnt/c`.** Building over the Windows mount is very slow,
case-insensitive, and has permission behaviour that breaks scons.

```bash
git clone --recurse-submodules -b test https://github.com/goldbattle/openpilot.git ~/openpilot
cd ~/openpilot
tools/op.sh setup          # submodules + tools/setup_dependencies.sh + uv sync --frozen --all-extras
source .venv/bin/activate
scons -j$(nproc)
```

Notes that differ from older openpilot docs: `tools/ubuntu_setup.sh` and
`tools/install_ubuntu_dependencies.sh` **do not exist** in this tree. Setup is
`tools/setup_dependencies.sh` (distro-agnostic, installs `build-essential curl
libcurl4-openssl-dev locales git xclip wl-clipboard`) plus `uv sync`; the Ubuntu version gate
lives in `tools/op.sh`. `.python-version` pins 3.12.13, which uv fetches itself.

## Repo layout

The git root holds `SConstruct`, `uv.lock`, `pyproject.toml`; the importable package is
`openpilot/` beneath it. So imports are `openpilot.common.params` run **from the git root**, and
test paths look like `openpilot/selfdrive/car/tests/…`. Submodules: `opendbc_repo`, `panda`,
`msgq_repo`, `rednose_repo`, `tinygrad_repo`, `teleoprtc_repo`.

## Running things

```bash
cd ~/openpilot && source .venv/bin/activate

python -c "from openpilot.common.params import Params; print(Params().get('Version'))"
pytest openpilot/selfdrive/car/tests/test_car_interfaces.py
pytest opendbc_repo/opendbc/safety/tests/test_toyota_xv40.py    # libsafety, needs cffi
scons -j$(nproc)                                                # after C++/capnp changes

# UI against a replayed route -- see "Running the UI locally" below
openpilot/tools/replay/replay --demo &
./openpilot/selfdrive/ui/ui.py
```

## Running the UI locally (the whole stack, no device)

WSLg gives WSL a display, so the pyray UI renders as a real window on the Windows desktop.
`scripts/run_ui.sh` does the whole dance — replay a route, wait for it to publish, open the UI:

```bash
wsl -d Ubuntu-24.04 -e bash /mnt/c/Users/Patrick/Code/openpilot/.claude/skills/local-dev/scripts/run_ui.sh
# or, inside WSL:  ~/.../local-dev/scripts/run_ui.sh [route-id]
```

The demo log has `deviceState.started = True`, so the UI goes **onroad on its own** and you see
exactly what the driver sees: road camera with the model's path/lane/lead overlay, the
driver-monitoring page one swipe right, the capture status line along the bottom. `BIG=1` gets
the large tici layout instead of mici.

What's actually verified locally this way: layout, swipe/scroll, the model and HUD renderers,
the driver-camera page, camera decode via VisionIPC. What is **not**: real camera exposure,
touch on a physical panel, anything gated on `deviceState` fields the demo log doesn't carry.

Environment facts (WSLg, verified 2026-07-22):
- Rendering is **software GL** — `llvmpipe` (Mesa), no `/dev/dri`, no GPU passthrough for GL.
  Fine for 536×240; don't expect device frame rates.
- raylib picks the **X11/GLFW** backend (Xwayland). Occasionally the window dies at startup with
  `XIO: fatal IO error 2 on X server ":0"`. That is transient Xwayland flakiness, not a code
  fault — rerun and it comes up. A clean run holds indefinitely.
- `wifi_manager.py` logs `Object path ('T') must start with /` on a loop. There is no
  NetworkManager in WSL; the DBus call is caught and retried in a daemon thread. Harmless noise,
  not a crash. The network settings panel just won't populate.

## Working on Windows without WSL

Still possible, and was the whole workflow before WSL existed. What substitutes for running
things:

- `opendbc`'s pure-Python layer imports fine after `pip install numpy pycapnp pycryptodome` —
  enough for platform registration, `DBC` map creation, `CarInterface.get_params()`.
- Validate every `ret.<field>` in a carstate against `car.CarState.new_message()` with `getattr`.
  This is what caught `ret.gas` being deprecated.
- `python .claude/skills/car-port-passive/scripts/dbc_check.py <dbc> --carstate <file>` — bit
  layouts, duplicates, carstate cross-reference, `COUNTER`/`CHECKSUM` name traps.
- `python -m py_compile <files>` to catch syntax errors before deploying.
- For C++: extract the changed block into a `.inc`, compile it against stubs with a local `g++`
  and assert on real values. Tests the actual code rather than a retyped copy.

**Beware `get_interface_attr()` on Windows**: it does `car_folder.split('/')[-1]` over `os.walk`
output, which never splits backslash paths, so every brand import raises and is swallowed —
`all_legacy_fingerprint_cars()` and `FW_VERSIONS` come back **empty**. That is a Windows artifact
of upstream code, not a regression. Don't chase it.

## Line endings

`core.autocrlf=true`, so the working tree is CRLF while git stores LF. Committed blobs are LF,
so deploying over git is always safe. **Never scp a shell script from the Windows tree** — the
`#!/usr/bin/env bash` shebang becomes `bash\r` and the device bricks at the boot logo. Python
tolerates CRLF; shell does not.

## Before you deploy

1. `python -m py_compile` on every changed Python file (catches the crash-loop class of bug)
2. Run any `demo()`/`_self_check()` in the files you touched
3. `pytest` the narrowest relevant test
4. `scons` if you touched C++ or capnp
5. Only then `comma-device` → `deploy --build --restart`

A Python exception in `process_config.py` is the worst case: every `ensure_running` call raises,
the manager crash-loops, and the device sits on the boot logo with no UI to fix it from — you're
recovering over SSH. Compile-check that file especially.

## Related

`dev-loop` for the overall workflow, `route-replay` to feed the local UI, `comma-device` to deploy once it's green.
