#!/usr/bin/env python3
"""One CLI for driving a comma device over SSH: deploy, restart, inspect, pull routes.

Runs on the dev box (Windows or WSL), NOT on the device. Uses plain `ssh`/`scp` subprocesses
so it has no openpilot imports -- openpilot can't even be imported on Windows (fcntl,
params_pyx), which is exactly why this exists.

Device IP resolution order: --ip, then $COMMA_IP, then error. Never hardcode an IP; the
device is on DHCP and the address changes.

  export COMMA_IP=10.0.0.35
  python comma.py status
  python comma.py deploy --branch test --build
  python comma.py routes
  python comma.py pull 00000024--018c2cdbb5 ./routes
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys

DEVICE_ROOT = "/data/openpilot"
REALDATA = "/data/media/0/realdata"
# The AGNOS venv python. Bare /usr/bin/python3 lacks capnp/pyray and will fail on any
# openpilot import; every device-side snippet below needs this one.
VENV_PY = "/usr/local/venv/bin/python3"
# scons and capnpc live in the venv bin too, and it is NOT on a non-login shell's PATH.
BUILD_PATH = "export PATH=/usr/local/venv/bin:$PATH"


def die(msg: str) -> int:
  print(f"error: {msg}", file=sys.stderr)
  return 1


def resolve_ip(arg_ip: str | None) -> str:
  ip = arg_ip or os.environ.get("COMMA_IP")
  if not ip:
    raise SystemExit("no device IP: pass --ip or set COMMA_IP (the device is on DHCP)")
  return ip


def ssh_argv(ip: str, remote_cmd: str, tty: bool = False) -> list[str]:
  argv = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
  if tty:
    argv.append("-t")
  key = os.environ.get("COMMA_SSH_KEY")
  if key:
    argv += ["-i", key]
  return argv + [f"comma@{ip}", remote_cmd]


def run(ip: str, remote_cmd: str, check: bool = True, quiet: bool = False) -> str:
  """Run a command on the device, return stdout."""
  proc = subprocess.run(ssh_argv(ip, remote_cmd), capture_output=True, text=True)
  if proc.returncode != 0 and check:
    sys.stderr.write(proc.stderr)
    raise SystemExit(f"remote command failed ({proc.returncode}): {remote_cmd}")
  if not quiet and proc.stderr.strip():
    sys.stderr.write(proc.stderr)
  return proc.stdout


def stream(ip: str, remote_cmd: str, tty: bool = False) -> int:
  """Run a command on the device with output streaming straight through."""
  return subprocess.run(ssh_argv(ip, remote_cmd, tty=tty)).returncode


def py(ip: str, snippet: str, check: bool = True) -> str:
  """Run a python snippet on the device under the venv interpreter.

  Quoting note: the snippet is passed via a heredoc rather than -c "..." so it can contain
  quotes of both kinds without escaping games.
  """
  cmd = (f"cd {DEVICE_ROOT} && PYTHONPATH={DEVICE_ROOT} {VENV_PY} - <<'PYEOF'\n"
         f"{snippet}\nPYEOF")
  return run(ip, cmd, check=check)


# ---------------------------------------------------------------- commands

def cmd_status(args) -> int:
  ip = resolve_ip(args.ip)
  git = run(ip, f"cd {DEVICE_ROOT} && git rev-parse --abbrev-ref HEAD && git rev-parse --short HEAD "
                f"&& (git diff --quiet && git diff --cached --quiet && echo clean || echo DIRTY)")
  lines = git.split()
  branch, commit, tree = (lines + ["?", "?", "?"])[:3]

  misc = run(ip, "uptime -p; df -h /data | tail -1; free -m | awk 'NR==2{print $3\"/\"$2\" MB\"}'")

  snippet = """
import json
from openpilot.cereal import messaging
sm = messaging.SubMaster(['deviceState', 'pandaStates', 'carState', 'carParams'])
for _ in range(60):
  sm.update(100)
  if sm.updated['deviceState'] and sm.updated['pandaStates']:
    break
ds = sm['deviceState']
ps = sm['pandaStates'][0] if len(sm['pandaStates']) else None
out = {
  'started': bool(ds.started),
  'freeSpacePercent': round(ds.freeSpacePercent, 1),
  'thermalStatus': str(ds.thermalStatus),
  'networkType': str(ds.networkType),
}
if ps is not None:
  out.update({
    'pandaType': str(ps.pandaType),
    'ignitionLine': bool(ps.ignitionLine),
    'ignitionCan': bool(ps.ignitionCan),
    'safetyModel': str(ps.safetyModel),
    'safetyParam': ps.safetyParam,
    'voltage_mV': ps.voltage,
    'bus1_rx': ps.canRxErrs if hasattr(ps, 'canRxErrs') else None,
  })
out['canValid'] = bool(sm['carState'].canValid)
out['carFingerprint'] = str(sm['carParams'].carFingerprint)
print(json.dumps(out, indent=2))
"""
  live = py(ip, snippet, check=False)

  print(f"device      {ip}")
  print(f"branch      {branch} @ {commit} ({tree})")
  for line in misc.strip().splitlines():
    print(f"            {line.strip()}")
  print("--- live state ---")
  print(live.strip() or "(no messages -- is openpilot running? try `procs`)")
  return 0


def cmd_procs(args) -> int:
  ip = resolve_ip(args.ip)
  snippet = """
from openpilot.cereal import messaging
sm = messaging.SubMaster(['managerState'])
for _ in range(60):
  sm.update(100)
  if sm.updated['managerState']:
    break
procs = sorted(sm['managerState'].processes, key=lambda p: p.name)
if not procs:
  print('no managerState -- manager may be down')
for p in procs:
  print(f"{'RUN ' if p.running else '    '} {p.name:24s} pid={p.pid}")
"""
  print(py(ip, snippet, check=False).strip())
  # Independent of cereal: catches the case where manager itself is dead.
  print("\n--- upload/telemetry procs (MUST be empty on this fork) ---")
  out = run(ip, "ps aux | grep -E 'uploader|athena' | grep -v grep || true")
  print(out.strip() or "(none -- correct)")
  return 0


def cmd_deploy(args) -> int:
  ip = resolve_ip(args.ip)
  branch = args.branch

  # Refuse to deploy something that isn't pushed: the device pulls from the fork remote,
  # so an unpushed local commit would silently deploy the PREVIOUS commit.
  if not args.no_check:
    local = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    remote = subprocess.run(["git", "rev-parse", f"origin/{branch}"], capture_output=True, text=True).stdout.strip()
    if not remote:
      return die(f"no origin/{branch} locally -- run `git fetch origin` first")
    if local != remote:
      return die(f"local HEAD {local[:9]} != origin/{branch} {remote[:9]}. "
                 f"Push first (git push origin HEAD:{branch}), or pass --no-check.")

  # A dirty device tree makes `git reset --hard` throw away on-device debugging edits
  # silently; make that visible rather than surprising.
  dirty = run(ip, f"cd {DEVICE_ROOT} && git status --porcelain", check=False).strip()
  if dirty and not args.force:
    print("device tree is dirty:")
    print(dirty)
    return die("refusing to reset over it; re-run with --force to discard")

  print(f"==> fetching origin/{branch} on device")
  stream(ip, f"cd {DEVICE_ROOT} && git fetch origin && git reset --hard origin/{branch} "
             f"&& git submodule update --init --recursive && git rev-parse --short HEAD")

  if args.build:
    print("==> scons (this is a slow arm build; C++/capnp changes need it)")
    rc = stream(ip, f"cd {DEVICE_ROOT} && {BUILD_PATH} && scons -j4")
    if rc != 0:
      return die("scons failed -- do NOT restart, the device would come up broken")

  if args.restart:
    print("==> restarting openpilot (~40s)")
    stream(ip, "sudo systemctl restart comma")
  else:
    print("note: not restarted. Changes are on disk but the running manager has stale code.")
    print("      run `comma.py restart` when ready.")
  return 0


def cmd_restart(args) -> int:
  ip = resolve_ip(args.ip)
  return stream(ip, "sudo systemctl restart comma")


def cmd_reboot(args) -> int:
  ip = resolve_ip(args.ip)
  return stream(ip, "sudo reboot", tty=False)


def cmd_build(args) -> int:
  ip = resolve_ip(args.ip)
  return stream(ip, f"cd {DEVICE_ROOT} && {BUILD_PATH} && scons -j{args.jobs}")


def cmd_logs(args) -> int:
  ip = resolve_ip(args.ip)
  # The manager runs inside tmux session `comma`; its pane holds every proc's stdout.
  if args.unit:
    return stream(ip, f"journalctl -u comma -n {args.lines} {'-f' if args.follow else ''}")
  cmd = f"tmux capture-pane -p -S -{args.lines} -t comma 2>/dev/null || journalctl -u comma -n {args.lines}"
  if args.grep:
    cmd += f" | grep -i {shlex.quote(args.grep)}"
  return stream(ip, cmd)


def cmd_params(args) -> int:
  ip = resolve_ip(args.ip)
  if args.value is None:
    snippet = f"""
from openpilot.common.params import Params
v = Params().get({args.key!r})
print(repr(v))
"""
  else:
    snippet = f"""
from openpilot.common.params import Params
Params().put({args.key!r}, {args.value!r})
print('set', {args.key!r}, '=', {args.value!r})
"""
  print(py(ip, snippet).strip())
  return 0


def cmd_routes(args) -> int:
  ip = resolve_ip(args.ip)
  snippet = f"""
import os
root = {REALDATA!r}
segs = {{}}
for d in sorted(os.listdir(root)):
  full = os.path.join(root, d)
  if not os.path.isdir(full) or '--' not in d:
    continue
  route = d.rsplit('--', 1)[0]
  size = sum(os.path.getsize(os.path.join(full, f)) for f in os.listdir(full)
             if os.path.isfile(os.path.join(full, f)))
  locked = any(f.endswith('.lock') for f in os.listdir(full))
  r = segs.setdefault(route, [0, 0, False])
  r[0] += 1
  r[1] += size
  r[2] = r[2] or locked
for route, (n, size, locked) in sorted(segs.items()):
  num = int(route.split('--')[0], 16)
  print(f"#{{num:<4}} {{route}}  {{n:>3}} seg  {{size/1024**3:>6.2f}} GB  {{'RECORDING' if locked else ''}}")
if not segs:
  print('(no routes)')
"""
  print(py(ip, snippet).strip())
  return 0


def cmd_pull(args) -> int:
  ip = resolve_ip(args.ip)
  dest = os.path.abspath(args.dest)
  os.makedirs(dest, exist_ok=True)
  # rsync isn't guaranteed on AGNOS; scp -r over the segment glob is.
  print(f"==> pulling {args.route}* into {dest}")
  key = os.environ.get("COMMA_SSH_KEY")
  argv = ["scp", "-r"] + (["-i", key] if key else []) + \
         [f"comma@{ip}:{REALDATA}/{args.route}--*", dest]
  return subprocess.run(argv).returncode


def cmd_can(args) -> int:
  ip = resolve_ip(args.ip)
  snippet = f"""
import time
from collections import Counter
from openpilot.cereal import messaging
sm = messaging.SubMaster(['can'])
addrs = Counter()
buses = Counter()
frames = 0
t0 = time.monotonic()
while time.monotonic() - t0 < {args.seconds}:
  sm.update(100)
  if not sm.updated['can']:
    continue
  for f in sm['can']:
    frames += 1
    addrs[hex(f.address)] += 1
    buses[f.src] += 1
dt = time.monotonic() - t0
print(f"{{frames}} frames in {{dt:.1f}}s ({{frames/dt:.0f}}/s), {{len(addrs)}} unique addrs")
print("by bus:", dict(buses))
print("top addrs:", addrs.most_common(20))
if frames == 0:
  print()
  print("NO CAN. Check, in order:")
  print("  1. ignition/key -- pandaStates.ignitionCan (comma.py status)")
  print("  2. OBD multiplexing: safetyModel must be elm327 with safetyParam 0")
  print("  3. the panda is powered and not in bootstub")
"""
  print(py(ip, snippet).strip())
  return 0


def cmd_exec(args) -> int:
  ip = resolve_ip(args.ip)
  return stream(ip, " ".join(args.cmd), tty=True)


def main() -> int:
  p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  p.add_argument("--ip", help="device IP (default: $COMMA_IP)")
  sub = p.add_subparsers(dest="cmd", required=True)

  sub.add_parser("status", help="branch, commit, disk, ignition, panda health, canValid").set_defaults(fn=cmd_status)
  sub.add_parser("procs", help="managed processes + assert no uploader/athenad").set_defaults(fn=cmd_procs)

  d = sub.add_parser("deploy", help="fetch+reset the device to origin/<branch>")
  d.add_argument("--branch", default="test")
  d.add_argument("--build", action="store_true", help="run scons after pulling")
  d.add_argument("--restart", action="store_true", help="systemctl restart comma after")
  d.add_argument("--force", action="store_true", help="discard a dirty device tree")
  d.add_argument("--no-check", action="store_true", help="skip the local==origin check")
  d.set_defaults(fn=cmd_deploy)

  sub.add_parser("restart", help="systemctl restart comma (~40s)").set_defaults(fn=cmd_restart)
  sub.add_parser("reboot", help="full device reboot").set_defaults(fn=cmd_reboot)

  b = sub.add_parser("build", help="scons on device")
  b.add_argument("-j", "--jobs", type=int, default=4)
  b.set_defaults(fn=cmd_build)

  lg = sub.add_parser("logs", help="tail the manager tmux pane")
  lg.add_argument("-n", "--lines", type=int, default=200)
  lg.add_argument("-f", "--follow", action="store_true")
  lg.add_argument("-g", "--grep")
  lg.add_argument("-u", "--unit", action="store_true", help="journalctl -u comma instead of tmux")
  lg.set_defaults(fn=cmd_logs)

  pa = sub.add_parser("params", help="get/set a param")
  pa.add_argument("key")
  pa.add_argument("value", nargs="?")
  pa.set_defaults(fn=cmd_params)

  sub.add_parser("routes", help="list recorded routes").set_defaults(fn=cmd_routes)

  pl = sub.add_parser("pull", help="scp a route's segments to the dev box")
  pl.add_argument("route", help="route id, e.g. 00000024--018c2cdbb5")
  pl.add_argument("dest", nargs="?", default="./routes")
  pl.set_defaults(fn=cmd_pull)

  c = sub.add_parser("can", help="sample live CAN: rate, buses, addresses")
  c.add_argument("-s", "--seconds", type=float, default=5.0)
  c.set_defaults(fn=cmd_can)

  e = sub.add_parser("exec", help="run an arbitrary command on the device")
  e.add_argument("cmd", nargs=argparse.REMAINDER)
  e.set_defaults(fn=cmd_exec)

  args = p.parse_args()
  try:
    return args.fn(args)
  except SystemExit as ex:
    return die(str(ex)) if not isinstance(ex.code, int) else ex.code


if __name__ == "__main__":
  sys.exit(main())
