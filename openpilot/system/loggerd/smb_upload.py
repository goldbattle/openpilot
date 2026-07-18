#!/usr/bin/env python3
"""Upload recorded routes to a user-owned SMB share (recorder fork).

Independent of system/loggerd/uploader.py (which talks to comma's cloud and is
disabled in this fork) but reuses its directory-ordering helper and the same
xattr-based "is this file done" bookkeeping, under a different attribute name.
"""
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import smbclient

from openpilot.common.hardware.hw import Paths
from openpilot.common.swaglog import cloudlog
from openpilot.system.loggerd.uploader import listdir_by_creation
from openpilot.system.loggerd.xattr_cache import getxattr, setxattr

UPLOAD_ATTR_NAME = 'user.smb_upload'
UPLOAD_ATTR_VALUE = b'1'
PART_SUFFIX = '.part'
CHUNK_SIZE = 1024 * 1024  # 1MB

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
NO_NETWORK_POLL = 5.0
SESSION_TIMEOUT = 8.0  # give up fast on a bad host/share instead of hanging indefinitely
SMB_PORT = 445


def route_id(segment_dirname: str) -> str:
  """'<counter>--<random>--<segment#>' -> '<counter>--<random>'"""
  return segment_dirname.rsplit('--', 1)[0]


@dataclass
class RouteFile:
  path: str       # local absolute path
  rel_name: str   # "<segment_dir>/<filename>", used as the remote path under the share
  size: int

  @property
  def done(self) -> bool:
    return getxattr(self.path, UPLOAD_ATTR_NAME) == UPLOAD_ATTR_VALUE


@dataclass
class Route:
  id: str
  files: list[RouteFile] = field(default_factory=list)

  @property
  def total_size(self) -> int:
    return sum(f.size for f in self.files)

  @property
  def uploaded_size(self) -> int:
    return sum(f.size for f in self.files if f.done)

  @property
  def is_done(self) -> bool:
    return bool(self.files) and all(f.done for f in self.files)


def list_routes(root: str | None = None) -> list[Route]:
  """Group segment directories under Paths.log_root() into routes, oldest first.
  Skips any segment that's still being written (has an rlog.lock), same guard
  system/loggerd/uploader.py uses."""
  root = root or Paths.log_root()
  routes: dict[str, Route] = {}

  for logdir in listdir_by_creation(root):
    path = os.path.join(root, logdir)
    try:
      names = os.listdir(path)
    except OSError:
      continue

    if any(name.endswith('.lock') for name in names):
      continue  # still recording

    route = routes.setdefault(route_id(logdir), Route(route_id(logdir)))
    for name in sorted(names):
      if name.endswith(PART_SUFFIX):
        continue
      fn = os.path.join(path, name)
      try:
        size = os.path.getsize(fn)
      except OSError:
        continue
      route.files.append(RouteFile(fn, f"{logdir}/{name}", size))

  return [r for r in routes.values() if r.files]


def _unc_path(host: str, share_path: str, rel_name: str = "") -> str:
  parts = [share_path.strip('/\\')]
  if rel_name:
    parts += rel_name.replace('/', '\\').split('\\')
  return f"\\\\{host}\\" + "\\".join(p for p in parts if p)


def check_reachable(host: str, port: int = SMB_PORT, timeout: float = 3.0) -> float | None:
  """TCP-connect latency to the SMB port, in ms, or None if unreachable. A plain socket
  connect (not a full smbclient session) so it's cheap enough to poll every few seconds
  for a live status line."""
  if not host:
    return None
  start = time.monotonic()
  try:
    with socket.create_connection((host, port), timeout=timeout):
      pass
  except OSError:
    return None
  return (time.monotonic() - start) * 1000


def test_connection(host: str, share_path: str, username: str, password: str) -> str | None:
  """Try to reach the share. Returns an error message, or None on success."""
  try:
    smbclient.register_session(host, username=username or "", password=password or "", connection_timeout=SESSION_TIMEOUT)
    root = _unc_path(host, share_path)
    smbclient.makedirs(root, exist_ok=True)
    list(smbclient.scandir(root))
    return None
  except Exception as e:
    return str(e)


def upload_file(host: str, share_path: str, username: str, password: str,
                 rf: RouteFile, progress_cb: Callable[[int, int], None] | None = None) -> None:
  """Upload one file, never leaving a partial file visible under its final name:
  write to '<name>.part' and rename to the final name only once fully written."""
  if rf.done:
    if progress_cb:
      progress_cb(rf.size, rf.size)
    return

  remote_dir, remote_name = _unc_path(host, share_path, os.path.dirname(rf.rel_name)), os.path.basename(rf.rel_name)
  final_path = f"{remote_dir}\\{remote_name}"
  part_path = f"{final_path}{PART_SUFFIX}"

  # Recovery check: a previous run may have finished the write + rename but crashed
  # before the local xattr got set. Treat a same-size final file as already done
  # rather than re-uploading it.
  try:
    if smbclient.stat(final_path).st_size == rf.size:
      setxattr(rf.path, UPLOAD_ATTR_NAME, UPLOAD_ATTR_VALUE)
      if progress_cb:
        progress_cb(rf.size, rf.size)
      return
  except OSError:
    pass

  smbclient.makedirs(remote_dir, exist_ok=True)

  # A stale .part from an interrupted previous attempt is just overwritten from
  # scratch below (mode='wb') -- no byte-range resume.
  # ponytail: segments are at most a couple minutes of video, re-sending one file
  # is cheap; upgrade to range-resume only if that stops being true.
  written = 0
  with open(rf.path, 'rb') as local_f, smbclient.open_file(part_path, mode='wb') as remote_f:
    while chunk := local_f.read(CHUNK_SIZE):
      remote_f.write(chunk)
      written += len(chunk)
      if progress_cb:
        progress_cb(written, rf.size)

  try:
    smbclient.remove(final_path)
  except OSError:
    pass
  smbclient.rename(part_path, final_path)

  setxattr(rf.path, UPLOAD_ATTR_NAME, UPLOAD_ATTR_VALUE)


def next_file_to_upload(routes: list[Route]) -> Iterator[tuple[Route, RouteFile]]:
  for route in routes:
    if route.is_done:
      continue
    for f in route.files:
      if not f.done:
        yield route, f


def run(host: str, share_path: str, username: str, password: str, routes: list[Route],
        network_ok: Callable[[], bool], progress_cb: Callable[[Route, int, int], None],
        stop_event: threading.Event, on_error: Callable[[str], None] | None = None) -> None:
  """Upload every not-yet-done file across `routes`, oldest route first. Retries
  forever with capped exponential backoff on failure (bad connection, share down,
  Wi-Fi dropped mid-transfer, ...) until everything is done or stop_event is set."""
  try:
    smbclient.register_session(host, username=username or "", password=password or "", connection_timeout=SESSION_TIMEOUT)
  except Exception as e:
    # runs on a daemon thread -- an uncaught exception here would just silently kill it
    cloudlog.exception("smb register_session failed")
    if on_error:
      on_error(str(e))
    return

  backoff = INITIAL_BACKOFF
  while not stop_event.is_set():
    if not network_ok():
      stop_event.wait(NO_NETWORK_POLL)
      continue

    next_up = next(next_file_to_upload(routes), None)
    if next_up is None:
      return

    route, rf = next_up
    try:
      upload_file(host, share_path, username, password, rf,
                  progress_cb=lambda done, total: progress_cb(route, done, total))
      backoff = INITIAL_BACKOFF
    except Exception as e:
      cloudlog.exception("smb upload failed")
      if on_error:
        on_error(str(e))
      stop_event.wait(backoff)
      backoff = min(backoff * 2, MAX_BACKOFF)


def demo() -> None:
  """Self-check: route grouping + lock-skip logic against a fake local tree.
  Doesn't touch xattr/network, so it runs anywhere -- exercises everything in
  this module except the actual SMB transfer."""
  import tempfile

  with tempfile.TemporaryDirectory() as root:
    def make_segment(rid: str, seg: int, files: dict[str, bytes], lock: bool = False):
      d = os.path.join(root, f"{rid}--{seg}")
      os.makedirs(d, exist_ok=True)
      for name, data in files.items():
        with open(os.path.join(d, name), 'wb') as f:
          f.write(data)
      if lock:
        open(os.path.join(d, "rlog.lock"), 'w').close()

    make_segment("00000000--aaaaaaaaaa", 0, {"rlog.zst": b"a" * 10, "qlog.zst": b"b" * 5})
    make_segment("00000000--aaaaaaaaaa", 1, {"rlog.zst": b"c" * 10})
    make_segment("00000001--bbbbbbbbbb", 0, {"rlog.zst": b"d" * 20}, lock=True)  # still recording

    routes = list_routes(root)
    assert len(routes) == 1, f"expected the still-recording route to be skipped, got {routes}"
    assert routes[0].id == "00000000--aaaaaaaaaa"
    assert len(routes[0].files) == 3
    assert routes[0].total_size == 25
    assert route_id("00000000--aaaaaaaaaa--3") == "00000000--aaaaaaaaaa"

  print("smb_upload self-check OK")


if __name__ == "__main__":
  demo()
