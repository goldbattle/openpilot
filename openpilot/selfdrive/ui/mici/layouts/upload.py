import os
import threading
import time
import pyray as rl

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.scroller import Scroller, NavScroller
from openpilot.system.ui.widgets.label import gui_label, UnifiedLabel
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.widgets.button import BigTileButton
from openpilot.selfdrive.ui.mici.widgets.dialog import BigInputDialog
from openpilot.system.loggerd import smb_upload

NetworkType = log.DeviceState.NetworkType

ROUTE_POLL_INTERVAL = 2.0  # seconds
PING_POLL_INTERVAL = 3.0  # seconds
ROW_HEIGHT = 44
PAD = 10
LABEL_FRACTION = 0.45  # left column width, of row width


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
  """One route: short date/time label, and a percent (or checkmark once done)."""

  def __init__(self, route: smb_upload.Route):
    super().__init__()
    self.route = route
    self.set_rect(rl.Rectangle(0, 0, 0, ROW_HEIGHT))
    self._progress = 0.0  # live in-flight progress (0..1), separate from route.is_done
    self._checkmark = gui_app.texture("icons/checkmark.png", 22, 22)

  def set_parent_rect(self, parent_rect: rl.Rectangle) -> None:
    super().set_parent_rect(parent_rect)
    self._rect.width = parent_rect.width

  def set_progress(self, done: int, total: int) -> None:
    self._progress = (done / total) if total else 0.0

  def _label(self) -> str:
    # route id is "<counter>--<random>" -- the counter is a monotonic route number,
    # so pull that out plus a few chars of the random part; rows recorded close
    # together otherwise show the same minute and look identical.
    counter_hex, _, rand_hex = self.route.id.partition("--")
    try:
      route_num = int(counter_hex, 16)
    except ValueError:
      route_num = 0
    try:
      ctime = os.path.getctime(self.route.files[0].path)
      time_str = time.strftime("%H:%M:%S", time.localtime(ctime))
    except (OSError, IndexError):
      time_str = "--:--:--"
    return f"#{route_num}  {time_str}  {rand_hex[:4]}"

  def _render(self, rect: rl.Rectangle):
    rl.draw_line_ex(rl.Vector2(rect.x, rect.y + rect.height - 1), rl.Vector2(rect.x + rect.width, rect.y + rect.height - 1),
                    1.0, rl.Color(255, 255, 255, 35))
    gui_label(rl.Rectangle(rect.x + PAD, rect.y, rect.width * 0.5, rect.height), self._label(),
              font_size=20, font_weight=FontWeight.BOLD)

    if self.route.is_done:
      rl.draw_texture_ex(self._checkmark, rl.Vector2(rect.x + rect.width - PAD - self._checkmark.width,
                                                      rect.y + (rect.height - self._checkmark.height) / 2), 0, 1.0, rl.WHITE)
    else:
      status = f"{int(self._progress * 100)}%" if self._progress > 0 else "pending"
      gui_label(rl.Rectangle(rect.x + rect.width * 0.5, rect.y, rect.width * 0.5 - PAD, rect.height), status,
                font_size=18, color=rl.Color(180, 180, 180, 255), alignment=rl.GuiTextAlignment.TEXT_ALIGN_RIGHT)


class FileListPage(Scroller):
  """Route list only -- uploading is triggered from the next page over."""

  def __init__(self, parent: 'UploadPage'):
    super().__init__(horizontal=False, spacing=0, pad=8)
    self._parent = parent
    self._rows: dict[str, RouteRow] = {}
    self._empty_label = UnifiedLabel("no recordings yet", 24, FontWeight.DISPLAY, rl.WHITE,
                                     alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
                                     alignment_vertical=rl.GuiTextAlignmentVertical.TEXT_ALIGN_MIDDLE)

  def _refresh_rows(self):
    for route in self._parent.routes():
      row = self._rows.get(route.id)
      if row is None:
        row = RouteRow(route)
        self._rows[route.id] = row
        self._scroller.add_widget(row)
      else:
        row.route = route  # Route is rebuilt fresh each scan; swap in the latest done-state
      live = self._parent.progress_for(route.id)
      if live:
        row.set_progress(*live)

  def _update_state(self):
    super()._update_state()
    self._refresh_rows()

  def _render(self, rect: rl.Rectangle):
    if not self._parent.routes():
      self._empty_label.render(rect)
      return
    super()._render(rect)

  def at_top(self) -> bool:
    return self._scroller.scroll_panel.get_offset() >= -20


class UploadTileButton(BigTileButton):
  """BigTileButton with a hand-drawn upward arrow in the same top-right icon slot
  BigButton normally puts a texture icon -- there's no upload-arrow asset, so this
  is the only place in the UI that draws this particular glyph."""
  ICON_SIZE = 64  # matches the icon size other big buttons (SettingsBigButton etc.) use

  def __init__(self, width: float, height: float):
    super().__init__(width, height, "upload all")

  def _draw_content(self, btn_y: float):
    super()._draw_content(btn_y)  # draws the "upload all" label; icon=None so no texture drawn
    cx = self._rect.x + self._rect.width - 30 - self.ICON_SIZE / 2
    cy = btn_y + 30 + self.ICON_SIZE / 2
    s = self.ICON_SIZE * 0.8
    head_h = s * 0.45
    shaft_w = s * 0.28
    top = cy - s / 2
    rl.draw_triangle(rl.Vector2(cx, top), rl.Vector2(cx - s / 2, top + head_h), rl.Vector2(cx + s / 2, top + head_h), rl.WHITE)
    rl.draw_rectangle(int(cx - shaft_w / 2), int(top + head_h), int(shaft_w), int(s * 0.55), rl.WHITE)


class UploadTriggerPage(Widget):
  """The whole page is the button -- tap anywhere to upload everything not yet
  uploaded. No permanent caption; a message only appears while uploading or if
  something's actually wrong (missing config, connection failure, ...)."""
  MARGIN = 20

  def __init__(self, parent: 'UploadPage'):
    super().__init__()
    self._parent = parent
    self._btn = self._child(UploadTileButton(gui_app.width - self.MARGIN * 2, gui_app.height - self.MARGIN * 2))
    self._btn.set_click_callback(self._on_click)
    self._start_error: str | None = None

  def _on_click(self):
    ok, err = self._parent.start_upload()
    self._start_error = err if not ok else None

  def _status_text(self) -> str | None:
    if self._parent.is_uploading():
      return "uploading..."
    return self._start_error or self._parent.last_error()

  def at_top(self) -> bool:
    return True  # no scrollable content on this page

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.Color(0, 0, 0, 255))
    self._btn.set_position(rect.x + self.MARGIN, rect.y + self.MARGIN)
    self._btn.render()

    text = self._status_text()
    if text:
      is_error = not self._parent.is_uploading()
      gui_label(rl.Rectangle(rect.x + PAD, rect.y + rect.height - 32, rect.width - 2 * PAD, 26), text,
                font_size=18, alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
                color=rl.Color(220, 90, 90, 255) if is_error else rl.WHITE)


class ServerSettingsPage(Scroller):
  """Host / share path / username / password / Wi-Fi-only toggle, plus a live
  reachability + ping status line up top instead of a manual test button (a bad
  host with no timeout made the old button-triggered check look like a freeze).

  Extends Scroller directly (like FileListPage) rather than wrapping a child
  Scroller -- rows added through a nested Scroller's private ._scroller bypass
  the parent-disables-child touch-valid chain (_Scroller.add_widget wires each
  item's touch validity to that _Scroller's own .enabled, not to whatever's
  above it), so they kept accepting taps underneath the on-screen keyboard."""

  def __init__(self):
    super().__init__(horizontal=False, spacing=0, pad=8)
    self._params = Params()
    if self._params.get("SmbWifiOnly") is None:
      self._params.put_bool("SmbWifiOnly", True)  # default on: don't upload over cellular by surprise

    self._status_text = "checking..."
    self._pending_status: str | None = None
    threading.Thread(target=self._ping_worker, daemon=True).start()

    self._host_row = self._make_field_row("host", "SmbHost", "enter host or IP...")
    self._share_row = self._make_field_row("share path", "SmbSharePath", "enter share name...")
    self._user_row = self._make_field_row("username", "SmbUsername", "enter username, blank for guest...")
    self._pass_row = _Row("password", self._edit_password)
    self._wifi_row = _ToggleRow("Wi-Fi only", "SmbWifiOnly")

    self._scroller.add_widgets([self._host_row, self._share_row, self._user_row, self._pass_row, self._wifi_row])

  def _ping_worker(self):
    while True:
      host = self._params.get("SmbHost") or ""
      if not host:
        self._pending_status = "set a host below to check reachability"
      else:
        ms = smb_upload.check_reachable(host)
        self._pending_status = f"reachable  {ms:.0f}ms" if ms is not None else "unreachable"
      time.sleep(PING_POLL_INTERVAL)

  def _update_state(self):
    super()._update_state()
    if self._pending_status is not None:
      self._status_text = self._pending_status
      self._pending_status = None
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

  def at_top(self) -> bool:
    return self._scroller.scroll_panel.get_offset() >= -20


class UploadPage(NavScroller):
  """Swipeable: file list -> upload-all button -> server settings."""

  def __init__(self):
    super().__init__()
    # NavScroller can't take Scroller kwargs through its constructor (NavWidget.__init__
    # doesn't forward them), so configure the inner _Scroller directly -- same snap/no-gap
    # setup the main recorder pages use in main.py.
    self._scroller._snap_items = True
    self._scroller._spacing = 0
    self._scroller._pad = 0

    self._params = Params()
    self._routes: list[smb_upload.Route] = []
    self._pending_routes: list[smb_upload.Route] | None = None
    threading.Thread(target=self._scan_worker, daemon=True).start()

    self._upload_thread: threading.Thread | None = None
    self._stop_event = threading.Event()
    self._live_progress: dict[str, tuple[int, int]] = {}
    self._progress_lock = threading.Lock()
    self._last_error: str | None = None

    self._file_list = FileListPage(self)
    self._trigger = UploadTriggerPage(self)
    self._settings = ServerSettingsPage()
    self._pages = [self._file_list, self._trigger, self._settings]
    for page in self._pages:
      page.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
    self._scroller.add_widgets(self._pages)

  def _back_enabled(self) -> bool:
    # NavScroller's default lets a horizontal scroller always dismiss regardless of
    # scroll position -- fine when pages don't scroll themselves, but each page here
    # has its own vertical list, so swiping up through a long list (a "swipe down"
    # gesture) was also arming the dismiss-the-whole-page swipe. Only allow it once
    # the active page's own list is scrolled to the top (same rule NavRawScrollPanel/
    # the regulatory-info page uses).
    offset = -self._scroller.scroll_panel.get_offset()
    idx = max(0, min(round(offset / gui_app.width), len(self._pages) - 1)) if gui_app.width else 0
    return self._pages[idx].at_top()

  def _scan_worker(self):
    while True:
      try:
        self._pending_routes = smb_upload.list_routes()
      except Exception:
        cloudlog.exception("smb list_routes failed")
      time.sleep(ROUTE_POLL_INTERVAL)

  def _update_state(self):
    super()._update_state()
    if self._pending_routes is not None:
      self._routes = self._pending_routes
      self._pending_routes = None

  def routes(self) -> list[smb_upload.Route]:
    return self._routes

  def progress_for(self, route_id: str) -> tuple[int, int] | None:
    with self._progress_lock:
      return self._live_progress.get(route_id)

  def is_uploading(self) -> bool:
    return self._upload_thread is not None and self._upload_thread.is_alive()

  def last_error(self) -> str | None:
    return self._last_error

  def wifi_ok(self) -> bool:
    if not self._params.get_bool("SmbWifiOnly"):
      return True
    return ui_state.sm["deviceState"].networkType == NetworkType.wifi

  def start_upload(self) -> tuple[bool, str | None]:
    if self.is_uploading():
      return False, "already uploading"
    host = self._params.get("SmbHost") or ""
    share = self._params.get("SmbSharePath") or ""
    if not host or not share:
      return False, "set host + share path first"
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
      smb_upload.run(host, share, username, password, self._routes, self.wifi_ok, progress_cb, self._stop_event, on_error=on_error)

    self._upload_thread = threading.Thread(target=worker, daemon=True)
    self._upload_thread.start()
    return True, None


if __name__ == "__main__":
  gui_app.init_window("upload")
  page = UploadPage()
  for _ in gui_app.render():
    ui_state.update()
    page.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
