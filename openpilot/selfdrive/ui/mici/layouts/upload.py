import os
import threading
import time
import pyray as rl

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.system.ui.widgets.label import gui_label, UnifiedLabel
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.widgets.dialog import BigInputDialog
from openpilot.selfdrive.ui.mici.layouts.recorder import is_recording
from openpilot.system.loggerd import smb_upload

NetworkType = log.DeviceState.NetworkType

ROUTE_POLL_INTERVAL = 2.0  # seconds
PING_POLL_INTERVAL = 3.0  # seconds
# Auto-upload retry spacing. smb_upload.run() has its own per-file backoff, but it returns
# immediately if the session can't even be established (bad host, wrong password), so
# without this a misconfigured share would respawn the worker every single frame.
AUTO_RETRY_INTERVAL = 60.0
ROW_HEIGHT = 44
PAD = 10
LABEL_FRACTION = 0.45  # left column width, of row width
# RouteRow is three columns: label | size | status. Sizes are right-aligned so the
# numbers line up down the list.
ROUTE_LABEL_FRACTION = 0.58
ROUTE_SIZE_FRACTION = 0.80


def fmt_size(num_bytes: int) -> str:
  """Compact byte size, e.g. '1.2G'. Segments run to hundreds of MB, so K is the
  smallest unit worth showing."""
  size = float(num_bytes)
  for unit in ("K", "M", "G"):
    size /= 1024.0
    if size < 1024.0 or unit == "G":
      return f"{size:.1f}{unit}"
  return f"{size:.1f}G"


def draw_up_arrow(cx: float, cy: float, size: float, color: rl.Color = rl.WHITE) -> None:
  """Upload glyph, centered on (cx, cy). Drawn rather than a texture -- there's no
  upward-arrow asset in icons_mici/ and this is the only place that needs one."""
  head_h = size * 0.45
  shaft_w = size * 0.28
  top = cy - size / 2
  rl.draw_triangle(rl.Vector2(cx, top), rl.Vector2(cx - size / 2, top + head_h),
                   rl.Vector2(cx + size / 2, top + head_h), color)
  rl.draw_rectangle(int(cx - shaft_w / 2), int(top + head_h), int(shaft_w), int(size * 0.55), color)


class _Row(Widget):
  """One compact single-line row: label left, value right, tap anywhere to act.
  Sized for the real screen (536x240) -- a card-style button is most of the screen."""
  def __init__(self, label: str, on_click=None):
    super().__init__()
    self._label = label
    self._value = ""
    self.set_rect(rl.Rectangle(0, 0, 0, ROW_HEIGHT))
    if on_click:
      self.set_click_callback(on_click)

  def set_value(self, value: str) -> None:
    self._value = value

  def set_parent_rect(self, parent_rect: rl.Rectangle) -> None:
    super().set_parent_rect(parent_rect)
    self._rect.width = parent_rect.width

  def _render(self, rect: rl.Rectangle):
    rl.draw_line_ex(rl.Vector2(rect.x, rect.y + rect.height - 1), rl.Vector2(rect.x + rect.width, rect.y + rect.height - 1),
                    1.0, rl.Color(255, 255, 255, 35))
    gui_label(rl.Rectangle(rect.x + PAD, rect.y, rect.width * LABEL_FRACTION, rect.height), self._label,
              font_size=20, font_weight=FontWeight.BOLD)
    gui_label(rl.Rectangle(rect.x + rect.width * LABEL_FRACTION, rect.y, rect.width * (1 - LABEL_FRACTION) - PAD, rect.height),
              self._value, font_size=18, color=rl.Color(180, 180, 180, 255), alignment=rl.GuiTextAlignment.TEXT_ALIGN_RIGHT)


class _ToggleRow(Widget):
  """Compact param-bound on/off row -- system/ui/widgets/toggle.py's Toggle is still
  160x80, too big to sit next to a label on this screen, so this draws a small pill."""
  PILL_W, PILL_H = 52, 26

  def __init__(self, label: str, param: str):
    super().__init__()
    self._label = label
    self._param = param
    self._params = Params()
    self.set_rect(rl.Rectangle(0, 0, 0, ROW_HEIGHT))

  def set_parent_rect(self, parent_rect: rl.Rectangle) -> None:
    super().set_parent_rect(parent_rect)
    self._rect.width = parent_rect.width

  def _handle_mouse_release(self, mouse_pos):
    super()._handle_mouse_release(mouse_pos)
    self._params.put_bool(self._param, not self._params.get_bool(self._param), block=True)

  def _render(self, rect: rl.Rectangle):
    rl.draw_line_ex(rl.Vector2(rect.x, rect.y + rect.height - 1), rl.Vector2(rect.x + rect.width, rect.y + rect.height - 1),
                    1.0, rl.Color(255, 255, 255, 35))
    gui_label(rl.Rectangle(rect.x + PAD, rect.y, rect.width - self.PILL_W - PAD * 3, rect.height), self._label,
              font_size=20, font_weight=FontWeight.BOLD)

    checked = self._params.get_bool(self._param)
    pill_rect = rl.Rectangle(rect.x + rect.width - PAD - self.PILL_W, rect.y + (rect.height - self.PILL_H) / 2,
                             self.PILL_W, self.PILL_H)
    rl.draw_rectangle_rounded(pill_rect, 1.0, 8, rl.Color(51, 171, 76, 255) if checked else rl.Color(60, 60, 60, 255))
    knob_r = self.PILL_H / 2 - 3
    knob_x = pill_rect.x + self.PILL_H / 2 + (checked * (self.PILL_W - self.PILL_H))
    rl.draw_circle(int(knob_x), int(pill_rect.y + self.PILL_H / 2), knob_r, rl.WHITE)


class RouteRow(Widget):
  """One route: number + recording time + hash, and a percent (or checkmark once done)."""

  def __init__(self, route: smb_upload.Route):
    super().__init__()
    self.route = route
    self.set_rect(rl.Rectangle(0, 0, 0, ROW_HEIGHT))
    self._progress = 0.0  # live in-flight progress (0..1), separate from route.is_done
    self._checkmark = gui_app.texture("icons/checkmark.png", 22, 22)

    # route id is "<counter>--<random>"; the counter is a monotonic route number, used
    # both in the label and to sort newest-first in RouteListPage
    counter_hex, _, rand_hex = route.id.partition("--")
    try:
      self.route_num = int(counter_hex, 16)
    except ValueError:
      self.route_num = 0

    # Computed once: neither the recording time nor the id ever change, and this would
    # otherwise stat every file of every route on every frame.
    # NOTE: mtime, not ctime -- ctime is the inode-change time, which our own upload
    # marker (setxattr in smb_upload) bumps, so ctime would show the UPLOAD time.
    try:
      recorded_at = min(os.path.getmtime(f.path) for f in route.files)
      time_str = time.strftime("%m/%d %H:%M:%S", time.localtime(recorded_at))
    except (OSError, ValueError):
      time_str = "--/-- --:--:--"
    self._label = f"#{self.route_num}  {time_str}  {rand_hex}"

  def set_parent_rect(self, parent_rect: rl.Rectangle) -> None:
    super().set_parent_rect(parent_rect)
    self._rect.width = parent_rect.width

  def set_progress(self, done: int, total: int) -> None:
    self._progress = (done / total) if total else 0.0

  def _render(self, rect: rl.Rectangle):
    rl.draw_line_ex(rl.Vector2(rect.x, rect.y + rect.height - 1), rl.Vector2(rect.x + rect.width, rect.y + rect.height - 1),
                    1.0, rl.Color(255, 255, 255, 35))
    gui_label(rl.Rectangle(rect.x + PAD, rect.y, rect.width * ROUTE_LABEL_FRACTION, rect.height), self._label,
              font_size=20, font_weight=FontWeight.BOLD)

    # total_size is summed from the sizes list_routes already stat'd, so this is just
    # adding ints -- no filesystem work per frame. Read off self.route (not cached) because
    # the scan swaps in a fresh Route as later segments of a still-growing route land.
    size_x = rect.x + rect.width * ROUTE_LABEL_FRACTION
    gui_label(rl.Rectangle(size_x, rect.y, rect.width * (ROUTE_SIZE_FRACTION - ROUTE_LABEL_FRACTION), rect.height),
              fmt_size(self.route.total_size), font_size=18, color=rl.Color(180, 180, 180, 255),
              alignment=rl.GuiTextAlignment.TEXT_ALIGN_RIGHT)

    if self.route.is_done:
      rl.draw_texture_ex(self._checkmark, rl.Vector2(rect.x + rect.width - PAD - self._checkmark.width,
                                                      rect.y + (rect.height - self._checkmark.height) / 2), 0, 1.0, rl.WHITE)
    else:
      status = f"{int(self._progress * 100)}%" if self._progress > 0 else "pending"
      gui_label(rl.Rectangle(rect.x + rect.width * ROUTE_SIZE_FRACTION, rect.y,
                             rect.width * (1 - ROUTE_SIZE_FRACTION) - PAD, rect.height), status,
                font_size=18, color=rl.Color(180, 180, 180, 255), alignment=rl.GuiTextAlignment.TEXT_ALIGN_RIGHT)


def should_auto_upload(uploading: bool, recording: bool, wifi_ok: bool, configured: bool,
                       available: bool, has_pending: bool, now: float, next_attempt: float) -> bool:
  """Pure predicate so the gating is testable without a device (see _self_check).

  `recording` is the one that isn't just bookkeeping: loggerd is still appending segments
  to the live route, so uploading mid-recording would push a route that's missing its tail.
  list_routes already skips the segment holding an .lock, but that only protects the file
  being written -- the *route* is still incomplete until recording stops."""
  return (available and configured and wifi_ok and has_pending
          and not uploading and not recording and now >= next_attempt)


class UploadController:
  """Shared upload state (route scan, progress, the worker thread). Module singleton
  below, because the route list and the settings page are two separate nav-stack
  widgets that both need it -- same pattern as ui_state."""

  def __init__(self):
    self._params = Params()
    self._routes: list[smb_upload.Route] = []
    self._pending_routes: list[smb_upload.Route] | None = None
    self._upload_thread: threading.Thread | None = None
    self._stop_event = threading.Event()
    self._live_progress: dict[str, tuple[int, int]] = {}
    self._progress_lock = threading.Lock()
    self._last_error: str | None = None
    self._next_auto = 0.0
    threading.Thread(target=self._scan_worker, daemon=True).start()

  def _scan_worker(self):
    while True:
      try:
        self._pending_routes = smb_upload.list_routes()
      except Exception:
        cloudlog.exception("smb list_routes failed")
      time.sleep(ROUTE_POLL_INTERVAL)

  def update(self) -> None:
    """Called from a page's _update_state -- swaps in the latest scan on the UI thread."""
    if self._pending_routes is not None:
      self._routes = self._pending_routes
      self._pending_routes = None

  def tick(self) -> None:
    """Auto-upload driver. Driven from main.py's nav-stack tick rather than a page's
    _update_state, so it keeps running no matter which page is on screen -- and from the
    UI thread, so reading ui_state.sm (wifi_ok) doesn't race the thread that updates it."""
    self.update()
    if should_auto_upload(uploading=self.is_uploading(),
                          recording=is_recording(),
                          wifi_ok=self.wifi_ok(),
                          configured=bool(self._params.get("SmbHost") and self._params.get("SmbSharePath")),
                          available=smb_upload.available(),
                          has_pending=any(not r.is_done for r in self._routes),
                          now=time.monotonic(),
                          next_attempt=self._next_auto):
      self._next_auto = time.monotonic() + AUTO_RETRY_INTERVAL
      self.start_upload()

  def routes(self) -> list[smb_upload.Route]:
    return self._routes

  def progress_for(self, route_id: str) -> tuple[int, int] | None:
    with self._progress_lock:
      return self._live_progress.get(route_id)

  def is_uploading(self) -> bool:
    return self._upload_thread is not None and self._upload_thread.is_alive()

  def status_text(self) -> str:
    if self.is_uploading():
      return "uploading..."
    if self._last_error:
      return self._last_error
    if not self._routes:
      return "no recordings"
    pending = sum(1 for r in self._routes if not r.is_done)
    return "all uploaded" if pending == 0 else f"{pending} pending"

  def wifi_ok(self) -> bool:
    if not self._params.get_bool("SmbWifiOnly"):
      return True
    return ui_state.sm["deviceState"].networkType == NetworkType.wifi

  def start_upload(self) -> None:
    if self.is_uploading():
      return
    if not smb_upload.available():
      self._last_error = smb_upload.SMB_UNAVAILABLE
      return
    host = self._params.get("SmbHost") or ""
    share = self._params.get("SmbSharePath") or ""
    if not host or not share:
      self._last_error = "set host + share first"
      return
    username = self._params.get("SmbUsername") or ""
    password = self._params.get("SmbPassword") or ""

    self._stop_event = threading.Event()
    self._last_error = None

    def progress_cb(route: smb_upload.Route, done: int, total: int):
      with self._progress_lock:
        self._live_progress[route.id] = (done, total)

    def on_error(msg: str):
      self._last_error = msg

    def worker():
      smb_upload.run(host, share, username, password, self._routes, self.wifi_ok,
                     progress_cb, self._stop_event, on_error=on_error)

    self._upload_thread = threading.Thread(target=worker, daemon=True)
    self._upload_thread.start()


upload_controller = UploadController()


class RouteListPage(NavScroller):
  """The upload page: just the recorded routes and their upload status. Uploading is
  started from the SMB tile in settings."""

  def __init__(self):
    super().__init__(horizontal=False, spacing=0, pad=8)
    self._rows: dict[str, RouteRow] = {}
    self._empty_label = UnifiedLabel("no recordings yet", 24, FontWeight.DISPLAY, rl.WHITE,
                                     alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
                                     alignment_vertical=rl.GuiTextAlignmentVertical.TEXT_ALIGN_MIDDLE)

  def _update_state(self):
    super()._update_state()
    upload_controller.update()
    added = False
    for route in upload_controller.routes():
      row = self._rows.get(route.id)
      if row is None:
        row = RouteRow(route)
        self._rows[route.id] = row
        self._scroller.add_widget(row)
        added = True
      else:
        row.route = route  # Route is rebuilt fresh each scan; swap in the latest done-state
      live = upload_controller.progress_for(route.id)
      if live:
        row.set_progress(*live)

    # newest first. The controller keeps routes oldest-first (upload order, matching
    # uploader.py); only the display is reversed. Sorting _scroller.items in place is
    # the same thing MiciOffroadAlerts does to reorder by severity.
    if added:
      self._scroller.items.sort(key=lambda r: -r.route_num)

  def _render(self, rect: rl.Rectangle):
    if not upload_controller.routes():
      self._empty_label.render(rect)
      return
    super()._render(rect)


class SmbSettingsPage(NavScroller):
  """The settings tile: upload-all action plus the SMB server config. A live
  reachability + ping line sits at the top instead of a manual test button (a bad
  host with no timeout made the old button-triggered check look like a freeze)."""

  def __init__(self):
    super().__init__(horizontal=False, spacing=0, pad=8)
    self._params = Params()
    if self._params.get("SmbWifiOnly") is None:
      self._params.put_bool("SmbWifiOnly", True)  # default on: don't upload over cellular by surprise

    self._status_text = "checking..."
    self._pending_status: str | None = None
    threading.Thread(target=self._ping_worker, daemon=True).start()

    self._upload_row = _Row("upload all", upload_controller.start_upload)
    self._host_row = self._make_field_row("host", "SmbHost", "enter host or IP...")
    self._share_row = self._make_field_row("share path", "SmbSharePath", "enter share name...")
    self._user_row = self._make_field_row("username", "SmbUsername", "enter username, blank for guest...")
    self._pass_row = _Row("password", self._edit_password)
    self._wifi_row = _ToggleRow("Wi-Fi only", "SmbWifiOnly")

    self._scroller.add_widgets([self._upload_row, self._host_row, self._share_row,
                                self._user_row, self._pass_row, self._wifi_row])

  def _ping_worker(self):
    while True:
      host = self._params.get("SmbHost") or ""
      if not smb_upload.available():
        self._pending_status = smb_upload.SMB_UNAVAILABLE
      elif not host:
        self._pending_status = "set a host below"
      else:
        ms = smb_upload.check_reachable(host)
        self._pending_status = f"reachable  {ms:.0f}ms" if ms is not None else "unreachable"
      time.sleep(PING_POLL_INTERVAL)

  def _update_state(self):
    super()._update_state()
    upload_controller.update()
    if self._pending_status is not None:
      self._status_text = self._pending_status
      self._pending_status = None
    self._upload_row.set_value(upload_controller.status_text())
    self._pass_row.set_value("********" if self._params.get("SmbPassword") else "")

  def _make_field_row(self, label: str, param: str, hint: str) -> _Row:
    row = _Row(label)
    row.set_value(self._params.get(param) or "")

    def open_editor():
      dlg = BigInputDialog(hint, self._params.get(param) or "", minimum_length=0,
                           confirm_callback=lambda text: self._save_field(param, row, text))
      gui_app.push_widget(dlg)

    row.set_click_callback(open_editor)
    return row

  def _save_field(self, param: str, row: _Row, text: str):
    text = text.strip()
    if text:
      self._params.put(param, text)
    else:
      self._params.remove(param)
    row.set_value(text)

  def _edit_password(self):
    dlg = BigInputDialog("enter password, blank for guest...", "", minimum_length=0,
                         confirm_callback=self._save_password)
    gui_app.push_widget(dlg)

  def _save_password(self, text: str):
    if text:
      self._params.put("SmbPassword", text)
    else:
      self._params.remove("SmbPassword")

  def _render(self, rect: rl.Rectangle):
    reachable = self._status_text.startswith("reachable")
    gui_label(rl.Rectangle(rect.x + PAD, rect.y + 2, rect.width - 2 * PAD, 24), self._status_text,
              font_size=18, font_weight=FontWeight.BOLD,
              color=rl.Color(51, 171, 76, 255) if reachable else rl.Color(220, 90, 90, 255))
    self._scroller.render(rl.Rectangle(rect.x, rect.y + 28, rect.width, rect.height - 28))


def _self_check() -> None:
  assert fmt_size(0) == "0.0K", fmt_size(0)
  assert fmt_size(1536) == "1.5K", fmt_size(1536)
  assert fmt_size(5 * 1024**2) == "5.0M", fmt_size(5 * 1024**2)
  assert fmt_size(int(1.25 * 1024**3)) == "1.2G", fmt_size(int(1.25 * 1024**3))
  # must not roll over into a unit that isn't rendered
  assert fmt_size(4096 * 1024**3).endswith("G"), fmt_size(4096 * 1024**3)

  ok = dict(uploading=False, recording=False, wifi_ok=True, configured=True,
            available=True, has_pending=True, now=100.0, next_attempt=0.0)
  assert should_auto_upload(**ok)
  # every gate must independently block it
  assert not should_auto_upload(**{**ok, "recording": True}), "must not upload mid-recording"
  assert not should_auto_upload(**{**ok, "uploading": True}), "must not double-start"
  assert not should_auto_upload(**{**ok, "wifi_ok": False})
  assert not should_auto_upload(**{**ok, "configured": False})
  assert not should_auto_upload(**{**ok, "available": False})
  assert not should_auto_upload(**{**ok, "has_pending": False})
  assert not should_auto_upload(**{**ok, "now": 10.0, "next_attempt": 60.0}), "must respect backoff"
  assert should_auto_upload(**{**ok, "now": 60.0, "next_attempt": 60.0}), "backoff expires"
  print("upload self-check OK")


if __name__ == "__main__":
  import sys
  if "--check" in sys.argv:
    _self_check()
    raise SystemExit

  gui_app.init_window("upload")
  page = RouteListPage()
  gui_app.push_widget(page)
  for _ in gui_app.render():
    ui_state.update()
