"""recorder fork: pick which single camera to record, and start/stop the recording.

One camera at a time is deliberate: encoding three 20fps streams is the bulk of the device's
onroad write and heat budget, and a bench/calibration capture only ever wants one of them.
The selection is `RecordCamera` (read by loggerd/encoderd, see loggerd.h `camera_recorded`,
which defaults to road when unset) and the run/stop switch is `ForceOnroad` (read by hardwared,
which drives the whole onroad transition -- so IMU, GPS, CAN and the rlog are logged for every
capture regardless of which camera is picked).

Only the *encoders* are filtered, not camerad: all three sensors keep streaming while a
recording runs, which is what lets the preview at the end of this page show any of them.
"""
import pyray as rl
from collections.abc import Callable
from msgq.visionipc import VisionStreamType

from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
from openpilot.selfdrive.ui.mici.onroad import status_line
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigCircleButton, BigCircleToggle
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import gui_label, UnifiedLabel
from openpilot.system.ui.widgets.scroller import NavScroller

CAMERAS = ("road", "wide", "driver")
DEFAULT_CAMERA = CAMERAS[0]
STREAMS = {"road": VisionStreamType.VISION_STREAM_ROAD,
           "wide": VisionStreamType.VISION_STREAM_WIDE_ROAD,
           "driver": VisionStreamType.VISION_STREAM_DRIVER}


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


class RecordingTileButton(BigButton):
  """The developer-menu entry point: one rectangle filling the page, rather than a circle
  sitting among the adb/ssh toggles. BigButton's background is a fixed-size 402x180 texture,
  so this draws a rounded rect scaled to the widget instead and keeps everything else
  (label layout, press bounce). No status light -- each camera in the page behind it carries
  its own, and a second indicator here would just be another thing to keep in sync."""
  MARGIN = 20

  def __init__(self, on_click: Callable[[], None]):
    super().__init__("recording")
    self.set_rect(rl.Rectangle(0, 0, gui_app.width - self.MARGIN * 2, gui_app.height - self.MARGIN * 2))
    self.set_click_callback(on_click)

  def _render(self, _):
    _, btn_x, btn_y, scale = self._handle_background()
    scaled = rl.Rectangle(btn_x, btn_y, self._rect.width * scale, self._rect.height * scale)
    rl.draw_rectangle_rounded(scaled, 0.12, 14, rl.Color(255, 255, 255, int(255 * 0.08)))
    self._draw_content(btn_y)


class RecordingPreview(Widget):
  """The last item of the recording scroller: a live view of the selected camera, so you can
  frame the shot and confirm the capture without leaving the menu. It follows the selection
  rather than being three separate pages -- there is only ever one camera recording, and
  swiping past three previews to reach the one that matters is the annoying version."""
  SIZE = (496, 208)

  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, *self.SIZE))
    self._camera = DEFAULT_CAMERA
    self._view = self._child(CameraView("camerad", STREAMS[DEFAULT_CAMERA]))
    self._view._set_placeholder_color(rl.Color(20, 20, 20, 255))
    # camerad only runs while a recording is going, so the text below stands in for it
    self._idle_label = UnifiedLabel("", 32, FontWeight.DISPLAY,
                                    text_color=rl.Color(255, 255, 255, int(255 * 0.9)),
                                    alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
                                    alignment_vertical=rl.GuiTextAlignmentVertical.TEXT_ALIGN_MIDDLE)

  def set_camera(self, cam: str):
    if cam == self._camera:
      return
    self._camera = cam
    self._view.switch_stream(STREAMS[cam])

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rounded(rect, 0.15, 10, rl.BLACK)
    if not ui_state.started:
      # camerad is started by the onroad transition, which takes a couple of seconds after the
      # tap -- say so, or the wait looks like the preview is broken
      self._idle_label.set_text("starting camera..." if ui_state.force_onroad else
                                "pick a camera to\nstart recording")
      self._idle_label.render(rect)
      return

    rl.begin_scissor_mode(int(rect.x), int(rect.y), int(rect.width), int(rect.height))
    self._view.render(rect)
    status_line.render(rect, self._camera)
    rl.end_scissor_mode()
    rl.draw_rectangle_rounded_lines_ex(rect, 0.15, 10, 2, rl.Color(255, 255, 255, 60))


class RecordingLayoutMici(NavScroller):
  """One circle per camera, then a live preview of the selected one at the far right.
  Tapping an idle camera starts recording it; tapping the lit one stops. While a recording is
  running the other two are disabled -- switching camera means restarting loggerd/encoderd,
  which ends the route, so it has to be an explicit stop then start rather than something a
  single mis-tap can do mid-capture."""

  def __init__(self):
    super().__init__()
    # refreshed once per frame in _update_state rather than per button per draw: three
    # buttons each asking the light and the enabled state is six param file reads a frame.
    # Read straight from params, not ui_state's 5Hz cache, so a tap lights its button now.
    self._recording = False
    self._camera = DEFAULT_CAMERA
    self._preview = RecordingPreview()
    self._scroller.add_widgets([self._make_button(cam) for cam in CAMERAS] + [self._preview])

  def _update_state(self):
    super()._update_state()
    self._recording = ui_state.params.get_bool("ForceOnroad")
    self._camera = ui_state.params.get("RecordCamera") or DEFAULT_CAMERA
    self._preview.set_camera(self._camera)

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
    # starts it. Deliberately no navigation -- the point is to stay here and watch the preview,
    # and MiciMainLayout skips its usual onroad pop while ForceOnroad is set.
    ui_state.params.put("RecordCamera", cam, block=True)
    ui_state.params.put_bool("ForceOnroad", True, block=True)
    self._camera, self._recording = cam, True
