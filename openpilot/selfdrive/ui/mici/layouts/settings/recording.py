"""recorder fork: pick which single camera to record, and start/stop the recording.

One camera at a time is deliberate: encoding three 20fps streams is the bulk of the device's
onroad write and heat budget, and a bench/calibration capture only ever wants one of them.
The selection is `RecordCamera` (read by loggerd/encoderd, see loggerd.h `camera_recorded`,
which defaults to road when unset) and the run/stop switch is `ForceOnroad` (read by hardwared,
which drives the whole onroad transition -- so IMU, GPS, CAN and the rlog are logged for every
capture regardless of which camera is picked).

Only the *encoders* are filtered, not camerad: all three sensors keep streaming, so every
camera page still shows live video and you can watch any of them while one is being written.
"""
import pyray as rl
from collections.abc import Callable

from openpilot.selfdrive.ui.mici.widgets.button import BigCircleButton, BigCircleToggle
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos
from openpilot.system.ui.widgets.label import gui_label
from openpilot.system.ui.widgets.scroller import NavScroller

# left to right, matching the onroad camera pages in main.py
CAMERAS = ("road", "wide", "driver")
DEFAULT_CAMERA = CAMERAS[0]


class LabelCircleToggle(BigCircleToggle):
  """A circle button in the ADB/SSH style -- same background, press animation and status
  light -- but labelled with text instead of an icon (there are no road/wide/driver glyphs
  in icons_mici/), and with the light driven by external state rather than by its own tap.
  BigCircleToggle flips `_checked` on release, which is wrong here: whether the light is on
  is decided by the params, not by the fact that you touched the button."""

  def __init__(self, label: str, on_click: Callable[[], None], checked: Callable[[], bool]):
    # the icon is replaced by the label in _draw_content, so pass a 1px placeholder
    super().__init__(gui_app.texture("icons_mici/settings.png", 1, 1))
    self._label = label
    self._on_click = on_click
    self._checked_callback = checked

  def _handle_mouse_release(self, mouse_pos: MousePos):
    BigCircleButton._handle_mouse_release(self, mouse_pos)  # skip BigCircleToggle's self-toggle
    self._on_click()

  def _update_state(self):
    super()._update_state()
    self.set_checked(self._checked_callback())

  def _draw_content(self, btn_y: float):
    # the light, drawn exactly where BigCircleToggle puts it
    dot = self._txt_toggle_enabled if self._checked else self._txt_toggle_disabled
    rl.draw_texture_ex(dot, (self._rect.x + (self._rect.width - dot.width) / 2, btn_y + 5), 0, 1.0, rl.WHITE)

    alpha = 0.9 if self.enabled else 0.35
    gui_label(rl.Rectangle(self._rect.x, btn_y + self._rect.height * 0.45, self._rect.width, 60),
              self._label, font_size=44, font_weight=FontWeight.BOLD,
              alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
              color=rl.Color(255, 255, 255, int(255 * alpha)))


class RecordingLayoutMici(NavScroller):
  """One circle per camera. Tapping an idle one starts recording it; tapping the lit one
  stops. While a recording is running the other two are disabled -- switching camera means
  restarting loggerd/encoderd, which ends the route, so it has to be an explicit stop then
  start rather than something a single mis-tap can do mid-capture."""

  def __init__(self):
    super().__init__()
    # refreshed once per frame in _update_state rather than per button per draw: three
    # buttons each asking the light and the enabled state is six param file reads a frame
    self._recording = False
    self._camera = DEFAULT_CAMERA
    self._scroller.add_widgets([self._make_button(cam) for cam in CAMERAS])

  def _update_state(self):
    super()._update_state()
    self._recording = ui_state.params.get_bool("ForceOnroad")
    self._camera = ui_state.params.get("RecordCamera") or DEFAULT_CAMERA

  def _make_button(self, cam: str) -> LabelCircleToggle:
    def active() -> bool:
      return self._recording and self._camera == cam

    btn = LabelCircleToggle(cam, lambda: self._toggle(cam), active)
    # never interfere with a real ignition-driven drive: this only forces onroad from offroad
    btn.set_enabled(lambda: active() or (ui_state.is_offroad() and not self._recording))
    return btn

  def _toggle(self, cam: str):
    if self._recording and self._camera == cam:
      ui_state.params.put_bool("ForceOnroad", False, block=True)
      self._recording = False
      return

    # order matters: encoderd latches RecordCamera when it starts, and ForceOnroad is what
    # starts it. No navigation to do here -- MiciMainLayout already pops to the onroad view on
    # the offroad->onroad edge, and picks the page for the camera we just selected.
    ui_state.params.put("RecordCamera", cam, block=True)
    ui_state.params.put_bool("ForceOnroad", True, block=True)
    self._camera, self._recording = cam, True
