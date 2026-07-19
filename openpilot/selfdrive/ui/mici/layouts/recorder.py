import math
import time
import pyray as rl
from collections import deque
from collections.abc import Callable

from msgq.visionipc import VisionStreamType
from openpilot.common.params import Params
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos
from openpilot.system.ui.widgets.label import gui_label
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.widgets.button import BigCircleButton, BigTileButton
from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
from openpilot.selfdrive.ui.mici.onroad.driver_state import DriverStateRenderer

# Page order, left to right: RECORD, then the cameras. Starts on WIDE.
CAMERAS = [
  ("WIDE",   VisionStreamType.VISION_STREAM_WIDE_ROAD, False),
  ("ROAD",   VisionStreamType.VISION_STREAM_ROAD,      False),
  ("DRIVER", VisionStreamType.VISION_STREAM_DRIVER,    True),   # True -> driver monitoring overlay
]
START_PAGE = 2  # WIDE (pages are now SETTINGS, RECORD, then the cameras)

PAD = 8
DM_SIZE = 76
GRAVITY = 9.81


class _RecordingState:
  """The "Recording" param gates loggerd/encoderd/sensord in process_config.recording --
  a param is how this repo drives manager process predicates (cf. IsLiveStreaming,
  JoystickDebugMode). Cached because several widgets read it every frame and each
  get_bool is a file read; same idea as ui_state's PARAM_UPDATE_TIME."""
  REFRESH_S = 0.2

  def __init__(self):
    self._params = Params()
    self._value = False
    self._last_read = -1.0
    # monotonic, not time.time(): the device slews/jumps its wall clock on GPS+NTP sync,
    # which would make the elapsed counter leap or go negative mid-recording.
    self._started_at: float | None = None

  def _mark(self, recording: bool) -> None:
    """Start the clock on a False->True edge; leave it running otherwise so a repeated
    True (every poll) doesn't keep resetting elapsed to zero."""
    if recording and self._started_at is None:
      self._started_at = time.monotonic()
    elif not recording:
      self._started_at = None

  def get(self) -> bool:
    now = rl.get_time()
    if now - self._last_read > self.REFRESH_S:
      self._value = self._params.get_bool("Recording")
      self._last_read = now
      # catches edges from outside the UI too (manager clears it, or a CLI param write)
      self._mark(self._value)
    return self._value

  def elapsed(self) -> float | None:
    # ponytail: measured from when this UI process first saw Recording=True, not from when
    # loggerd actually started. Identical in the normal case (the UI is what sets the param);
    # only undercounts if the UI restarts mid-recording. Reading the true start would mean
    # stat'ing the current route's segments every frame -- not worth it for a status line.
    return None if self._started_at is None else time.monotonic() - self._started_at

  def set(self, recording: bool) -> None:
    self._params.put_bool("Recording", recording, block=True)
    self._value = recording  # reflect immediately so the button doesn't lag the tap
    self._last_read = rl.get_time()
    self._mark(recording)


_recording_state = _RecordingState()


def is_recording() -> bool:
  return _recording_state.get()


def set_recording(recording: bool) -> None:
  _recording_state.set(recording)


def fmt_elapsed(secs: float | None) -> str:
  """Elapsed recording time as h:mm:ss. Seconds are shown because h:mm alone reads a
  frozen "0:00" for the first full minute, which is indistinguishable from the recorder
  being broken -- the ticking digit is the only proof the button did anything.
  Hours are unbounded (no % 24): this is a session length, so 30h reads 30:00:00."""
  if secs is None:
    return ""
  total = int(secs)
  return f"{total // 3600}:{(total // 60) % 60:02d}:{total % 60:02d}"


def recording_elapsed_str() -> str:
  return fmt_elapsed(_recording_state.elapsed())


def _magnitude(v) -> float:
  return math.sqrt(sum(float(x) * float(x) for x in v))


class _SensorAverager:
  """Rolling 1s mean of the IMU magnitudes — the raw values (esp. gyro) are far too noisy to read."""
  WINDOW_S = 1.0

  def __init__(self):
    self._samples: deque[tuple[float, float, float]] = deque()
    self._last_frame = -1

  def update(self) -> None:
    # sensor_status() can be called by several pages in one frame (mid-swipe); sample only once
    if gui_app.frame == self._last_frame:
      return
    self._last_frame = gui_app.frame
    try:
      accel_g = _magnitude(ui_state.sm["accelerometer"].acceleration.v) / GRAVITY - 1.0
      gyro_dps = math.degrees(_magnitude(ui_state.sm["gyroscope"].gyroUncalibrated.v))
    except Exception:
      return
    now = rl.get_time()
    self._samples.append((now, accel_g, gyro_dps))
    while self._samples and now - self._samples[0][0] > self.WINDOW_S:
      self._samples.popleft()

  def means(self) -> tuple[float, float] | None:
    if not self._samples:
      return None
    n = len(self._samples)
    return sum(s[1] for s in self._samples) / n, sum(s[2] for s in self._samples) / n


_averager = _SensorAverager()


def sensor_status() -> str:
  """GPS fix (+accuracy once fixed) and 1s-averaged IMU: accel in g (gravity removed), gyro in deg/s."""
  parts = []
  try:
    gps = ui_state.sm["gpsLocationExternal"]
    parts.append(f"GPS {gps.horizontalAccuracy:.1f}m" if (gps.flags & 1) else "GPS --")
  except Exception:
    parts.append("GPS --")

  _averager.update()
  means = _averager.means()
  if means is not None:
    accel_g, gyro_dps = means
    parts.append(f"a {accel_g:+.1f}g")
    parts.append(f"w {gyro_dps:.0f}d/s")
  return "   ".join(parts)


class _RecorderPage(Widget):
  """Common: settings callback. Swipe-vs-tap is handled centrally — Scroller marks its items
  touch-invalid while scrolling and Widget._child() propagates that to nested widgets."""
  def __init__(self):
    super().__init__()
    self._on_settings_click: Callable | None = None
    self._on_upload_click: Callable | None = None
    self._on_record_start: Callable | None = None

  def set_callbacks(self, on_settings: Callable | None = None, on_upload: Callable | None = None, on_alerts: Callable | None = None,
                    alert_count_callback: Callable | None = None, max_severity_callback: Callable | None = None,
                    on_record_start: Callable | None = None):
    self._on_settings_click = on_settings
    self._on_upload_click = on_upload
    self._on_record_start = on_record_start

  def _open_settings(self):
    if self._on_settings_click:
      self._on_settings_click()

  def _open_upload(self):
    if self._on_upload_click:
      self._on_upload_click()


class CameraPage(_RecorderPage):
  """One full-screen camera stream. Swipe left/right (the Scroller) changes page."""
  def __init__(self, name: str, stream: VisionStreamType, driver_overlay: bool = False):
    super().__init__()
    self._name = name
    # cover=True -> crop to fill, no black bars either side
    self._camera = self._child(CameraView("camerad", stream, cover=True))
    self._camera._set_placeholder_color(rl.Color(20, 20, 20, 255))

    self._driver_state: DriverStateRenderer | None = None
    if driver_overlay:
      self._driver_state = self._child(DriverStateRenderer(lines=True))
      self._driver_state.set_rect(rl.Rectangle(0, 0, DM_SIZE, DM_SIZE))
      self._driver_state.load_icons()

  def _update_state(self):
    super()._update_state()
    if self._driver_state is not None:
      # without these the dmoji never draws (same as BaseDriverCameraDialog)
      self._driver_state.set_should_draw(True)
      self._driver_state.set_force_active(True)

  def _render(self, rect: rl.Rectangle):
    self._camera.render(rect)  # video fills the screen

    if self._driver_state is not None:  # small driver-monitor person, top right
      self._driver_state.set_position(rect.x + rect.width - DM_SIZE - PAD, rect.y + PAD)
      self._driver_state.render()

    # status line: camera name + GPS fix + |accel| (g) + |gyro| (deg/s)
    gui_label(rl.Rectangle(rect.x + PAD + 4, rect.y + 4, rect.width - 2 * PAD, 28),
              f"{self._name}   {sensor_status()}",
              font_size=22, font_weight=FontWeight.BOLD, color=rl.Color(255, 255, 255, 235))

    if is_recording():  # blinking REC while recording
      if int(rl.get_time() * 2) % 2 == 0:
        rl.draw_circle(int(rect.x + PAD + 12), int(rect.y + 40), 7, rl.RED)
      gui_label(rl.Rectangle(rect.x + PAD + 24, rect.y + 28, 180, 24), f"REC  {recording_elapsed_str()}",
                font_size=22, font_weight=FontWeight.BOLD, color=rl.RED)


class RecordCircleButton(BigCircleButton):
  """Circular record button: red dot = start, white square = stop. Red ring while recording."""
  def __init__(self, on_start: Callable | None = None):
    # icon is drawn by _draw_content below, so pass a 1px placeholder
    super().__init__(gui_app.texture("icons_mici/settings.png", 1, 1))
    self._on_start = on_start

  def _update_state(self):
    super()._update_state()
    self._red = is_recording()  # BigCircleButton picks the red background from this

  def _handle_mouse_release(self, mouse_pos: MousePos):
    super()._handle_mouse_release(mouse_pos)  # keeps the standard press animation/click delay
    starting = not is_recording()
    set_recording(starting)
    # On START, slide to the WIDE camera so you immediately see what's being captured.
    # Deliberately NOT on stop: jumping away then would hide the very state change you
    # pressed for, and you'd have no confirmation it actually stopped.
    if starting and self._on_start:
      self._on_start()

  def _draw_content(self, btn_y: float):
    cx = int(self._rect.x + self._rect.width / 2)
    cy = int(btn_y + self._rect.height / 2)
    if is_recording():
      s = 46  # square = stop
      rl.draw_rectangle(cx - s // 2, cy - s // 2, s, s, rl.WHITE)
    else:
      rl.draw_circle(cx, cy, 34, rl.RED)  # dot = record


class UploadCircleButton(BigCircleButton):
  """Circular upload button: hand-drawn upward arrow (not a Wi-Fi glyph -- this
  button opens the upload flow, it doesn't indicate Wi-Fi state)."""
  def __init__(self):
    # icon is drawn by _draw_content below, so pass a 1px placeholder
    super().__init__(gui_app.texture("icons_mici/settings.png", 1, 1))

  def _draw_content(self, btn_y: float):
    cx = self._rect.x + self._rect.width / 2
    cy = btn_y + self._rect.height / 2
    s = 64
    head_h = s * 0.45
    shaft_w = s * 0.28
    top = cy - s / 2
    rl.draw_triangle(rl.Vector2(cx, top), rl.Vector2(cx - s / 2, top + head_h), rl.Vector2(cx + s / 2, top + head_h), rl.WHITE)
    rl.draw_rectangle(int(cx - shaft_w / 2), int(top + head_h), int(shaft_w), int(s * 0.55), rl.WHITE)


class SettingsPage(_RecorderPage):
  """Its own page (swipe further left of RecordPage) -- the screen is only 536x240,
  too narrow to fit settings + upload + record side by side (see RecordPage). A big
  tile fills the page: same button background/press animation as everywhere else,
  just sized to the whole screen instead of BigButton's fixed 402x180."""
  MARGIN = 20

  def __init__(self):
    super().__init__()
    icon = gui_app.texture("icons_mici/settings.png", 64, 64)
    self._btn = self._child(BigTileButton(gui_app.width - self.MARGIN * 2, gui_app.height - self.MARGIN * 2, "settings", icon))
    self._btn.set_click_callback(self._open_settings)

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.Color(0, 0, 0, 255))
    self._btn.set_position(rect.x + self.MARGIN, rect.y + self.MARGIN)
    self._btn.render()


class RecordPage(_RecorderPage):
  """Upload (left) and record (right) buttons -- only 2 fit on this 536-wide screen."""
  def __init__(self):
    super().__init__()
    # standard widget behaviour: the buttons handle their own touches (and press animation),
    # swipe-cancellation comes from Scroller via Widget._child() propagation
    self._upload_btn = self._child(UploadCircleButton())
    self._record_btn = self._child(RecordCircleButton(on_start=self._start_recording))
    self._upload_btn.set_click_callback(self._open_upload)

  def _start_recording(self):
    if self._on_record_start:
      self._on_record_start()

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.Color(0, 0, 0, 255))

    bw = self._upload_btn.rect.width          # circular buttons are 180x180
    gap = 48
    x = rect.x + (rect.width - (bw * 2 + gap)) / 2
    y = rect.y + (rect.height - self._upload_btn.rect.height) / 2
    self._upload_btn.set_position(x, y)
    self._upload_btn.render()
    self._record_btn.set_position(x + bw + gap, y)
    self._record_btn.render()

    # Same blinking REC + elapsed as the camera pages. The button already swaps its glyph
    # (dot -> square) and ring colour, but that read as "nothing happened" on device, so
    # state gets stated in words here too rather than relying on the icon alone.
    if is_recording():
      if int(rl.get_time() * 2) % 2 == 0:
        rl.draw_circle(int(rect.x + PAD + 12), int(rect.y + 20), 7, rl.RED)
      gui_label(rl.Rectangle(rect.x + PAD + 24, rect.y + 8, rect.width - PAD * 2 - 24, 24),
                f"REC  {recording_elapsed_str()}", font_size=22, font_weight=FontWeight.BOLD, color=rl.RED)


def make_recorder_pages() -> list[Widget]:
  """Swipeable pages: SETTINGS | RECORD | WIDE | ROAD | DRIVER (starts on WIDE)."""
  params = Params()
  # record the driver camera too — loggerd reads this at startup to enable the dcamera encoder,
  # otherwise segments only contain fcamera (road) + ecamera (wide).
  params.put_bool("RecordFront", True)
  # note: DM procs are always_run in process_config (not gated on IsDriverViewEnabled, which
  # is CLEAR_ON_MANAGER_START and gets reset out from under us). dmonitoringd forces its
  # car-less demo path in this fork -- see the comment there.
  return [SettingsPage(), RecordPage()] + [CameraPage(n, s, dm) for n, s, dm in CAMERAS]


if __name__ == "__main__":
  # smoke test: left/right arrow keys page through, record toggle writes the flag file
  gui_app.init_window("recorder")
  pages = make_recorder_pages()
  idx = START_PAGE
  for _ in gui_app.render():
    ui_state.update()
    if rl.is_key_pressed(rl.KeyboardKey.KEY_RIGHT):
      idx = (idx + 1) % len(pages)
    if rl.is_key_pressed(rl.KeyboardKey.KEY_LEFT):
      idx = (idx - 1) % len(pages)
    pages[idx].render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
