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
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.widgets.button import BigCircleButton
from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
from openpilot.selfdrive.ui.mici.onroad.driver_state import DriverStateRenderer

# Page order, left to right: RECORD, then the cameras.
CAMERAS = [
  ("WIDE",   VisionStreamType.VISION_STREAM_WIDE_ROAD, False),
  ("ROAD",   VisionStreamType.VISION_STREAM_ROAD,      False),
  ("DRIVER", VisionStreamType.VISION_STREAM_DRIVER,    True),   # True -> driver monitoring overlay
]
RECORD_PAGE = 0
WIDE_PAGE = 1

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

  def mark(self, logging: bool) -> None:
    """Start the clock on a False->True edge; leave it running otherwise so a repeated
    True (every poll) doesn't keep resetting elapsed to zero."""
    if logging and self._started_at is None:
      self._started_at = time.monotonic()
    elif not logging:
      self._started_at = None

  def get(self) -> bool:
    now = rl.get_time()
    if now - self._last_read > self.REFRESH_S:
      self._value = self._params.get_bool("Recording")
      self._last_read = now
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
    self.mark(recording)


_recording_state = _RecordingState()


def is_recording() -> bool:
  """The manual record toggle -- the "Recording" param this UI owns."""
  return _recording_state.get()


def is_logging() -> bool:
  """True whenever loggerd is actually writing segments, which is what the REC indicator
  has to reflect. Two independent paths start it (see process_config): the manual param,
  and simply being onroad -- with the key on, `logging` is true and the drive is captured
  whether or not anyone pressed record. Showing only the param made the UI claim it wasn't
  recording during every ignition-driven drive."""
  logging = is_recording() or ui_state.started
  _recording_state.mark(logging)
  return logging


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


# Swipe-vs-tap on these pages is handled centrally: Scroller marks its items touch-invalid
# while scrolling and Widget._child() propagates that down to nested widgets.


class CameraPage(Widget):
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

    if is_logging():  # blinking REC whenever segments are being written
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
    self._red = is_logging()  # BigCircleButton picks the red background from this

  def _handle_mouse_release(self, mouse_pos: MousePos):
    super()._handle_mouse_release(mouse_pos)  # keeps the standard press animation/click delay
    if ui_state.started:
      # Onroad the drive is being logged off ignition, and clearing the param wouldn't stop
      # loggerd. Ignore the press rather than let the button claim a stop it can't perform.
      return
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
    if is_logging():
      s = 46  # square = stop
      rl.draw_rectangle(cx - s // 2, cy - s // 2, s, s, rl.WHITE)
    else:
      rl.draw_circle(cx, cy, 34, rl.RED)  # dot = record


class RecordPage(Widget):
  """Back-to-settings (left) and record (right) buttons -- only 2 fit on this 536-wide
  screen. The route list is not here: it lives under the upload panel in settings, since
  it's about what has been uploaded rather than about capturing."""
  def __init__(self, on_settings: Callable | None = None, on_record_start: Callable | None = None):
    super().__init__()
    # standard widget behaviour: the buttons handle their own touches (and press animation),
    # swipe-cancellation comes from Scroller via Widget._child() propagation
    self._settings_btn = self._child(BigCircleButton(gui_app.texture("icons_mici/settings.png", 64, 64)))
    self._record_btn = self._child(RecordCircleButton(on_start=on_record_start))
    if on_settings is not None:
      self._settings_btn.set_click_callback(on_settings)

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.Color(0, 0, 0, 255))

    bw = self._settings_btn.rect.width          # circular buttons are 180x180
    gap = 48
    x = rect.x + (rect.width - (bw * 2 + gap)) / 2
    y = rect.y + (rect.height - self._settings_btn.rect.height) / 2
    self._settings_btn.set_position(x, y)
    self._settings_btn.render()
    self._record_btn.set_position(x + bw + gap, y)
    self._record_btn.render()


class RecorderLayout(NavScroller):
  """recorder fork: the manual recorder, reached from settings like any other panel.
  Horizontal pages: RECORD (settings + record buttons), then the WIDE / ROAD / DRIVER feeds.
  Swipe down to go back, same as every other settings panel.

  This is a *manual* recorder for use with no key in the ignition. With the key on, the
  device goes onroad and loggerd records the drive on its own -- nothing here is needed."""
  def __init__(self):
    super().__init__(snap_items=True, spacing=0, pad=0, scroll_indicator=False, edge_shadows=False)

    # record the driver camera too — loggerd reads this at startup to enable the dcamera
    # encoder, otherwise segments only contain fcamera (road) + ecamera (wide).
    Params().put_bool("RecordFront", True)

    self._pages: list[Widget] = [RecordPage(on_settings=self.dismiss, on_record_start=self._show_wide)]
    self._pages += [CameraPage(n, s, dm) for n, s, dm in CAMERAS]
    for page in self._pages:
      page.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

    self._scroller.add_widgets(self._pages)
    self._scroller.set_reset_scroll_at_show(False)

  def _page_x(self, index: int) -> int:
    # pages are full-width with spacing=0, so page N starts exactly N screens along.
    # Computed rather than read off page.rect.x, which isn't laid out until first render.
    return int(self._rect.width * index)

  def _show_wide(self):
    # On START, slide to the WIDE camera so you immediately see what's being captured.
    self._scroller.scroll_to(self._page_x(WIDE_PAGE), smooth=True)

  def show_event(self):
    super().show_event()
    # Always open on the record button rather than wherever the last visit left off --
    # you came here from settings to start or stop a recording.
    self._scroller.scroll_to(self._page_x(RECORD_PAGE))
