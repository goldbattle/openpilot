import pyray as rl
from collections.abc import Callable

from msgq.visionipc import VisionStreamType
from openpilot.common.params import Params
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos
from openpilot.system.ui.widgets.label import gui_label
from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView

RECORDING_PARAM = "Recording"

# (label, stream) — tap the left/right half of the frame to cycle through these
CAMERAS = [
  ("ROAD",   VisionStreamType.VISION_STREAM_ROAD),
  ("WIDE",   VisionStreamType.VISION_STREAM_WIDE_ROAD),
  ("DRIVER", VisionStreamType.VISION_STREAM_DRIVER),
]

RECORD_BTN_SIZE = 168
GEAR_SIZE = 96


class RecordButton(Widget):
  """Circular button. Red dot = start recording, red square = stop (while recording)."""
  def __init__(self, toggle_cb: Callable[[bool], None]):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, RECORD_BTN_SIZE, RECORD_BTN_SIZE))
    self._toggle_cb = toggle_cb
    self._recording = False
    self._click_delay = 0.1

  def set_recording(self, recording: bool):
    self._recording = recording

  def _handle_mouse_release(self, mouse_pos: MousePos):
    super()._handle_mouse_release(mouse_pos)
    self._recording = not self._recording
    self._toggle_cb(self._recording)

  def _render(self, rect: rl.Rectangle):
    cx, cy = int(rect.x + rect.width / 2), int(rect.y + rect.height / 2)
    r = rect.width / 2
    press = 0.92 if self.is_pressed else 1.0
    rl.draw_circle(cx, cy, r * press, rl.Color(0, 0, 0, 120))
    rl.draw_circle_lines(cx, cy, int(r * press), rl.WHITE)
    if self._recording:
      s = int(r * 0.6 * press)                       # square = stop
      rl.draw_rectangle(cx - s // 2, cy - s // 2, s, s, rl.RED)
    else:
      rl.draw_circle(cx, cy, r * 0.58 * press, rl.RED)  # dot = record


class RecorderLayout(Widget):
  """Full-screen camera preview + start/stop recording. Drop-in for MiciHomeLayout."""
  def __init__(self):
    super().__init__()
    self._params = Params()
    self._cam_idx = 0
    self._camera = self._child(CameraView("camerad", CAMERAS[0][1]))
    self._camera._set_placeholder_color(rl.Color(20, 20, 20, 255))
    self._record_btn = self._child(RecordButton(self._on_toggle_record))
    self._record_btn.set_recording(self._params.get_bool(RECORDING_PARAM, False))
    self._settings_icon = gui_app.texture("icons_mici/settings.png", GEAR_SIZE, GEAR_SIZE)
    self._on_settings_click: Callable | None = None
    self._gear_rect = rl.Rectangle(0, 0, GEAR_SIZE, GEAR_SIZE)

  # signature-compatible with MiciHomeLayout.set_callbacks (only on_settings is used)
  def set_callbacks(self, on_settings: Callable | None = None, on_alerts: Callable | None = None,
                    alert_count_callback: Callable | None = None, max_severity_callback: Callable | None = None):
    self._on_settings_click = on_settings

  def _on_toggle_record(self, recording: bool):
    self._params.put_bool(RECORDING_PARAM, recording, block=True)

  def _cycle_camera(self, delta: int):
    self._cam_idx = (self._cam_idx + delta) % len(CAMERAS)
    self._camera.switch_stream(CAMERAS[self._cam_idx][1])

  def _handle_mouse_release(self, mouse_pos: MousePos):
    super()._handle_mouse_release(mouse_pos)
    if rl.check_collision_point_rec(mouse_pos, self._gear_rect):      # settings gear
      if self._on_settings_click:
        self._on_settings_click()
      return
    if rl.check_collision_point_rec(mouse_pos, self._record_btn.rect):  # button handles itself
      return
    # tap left/right half of the frame (above the button) to change camera
    if mouse_pos.y < self.rect.y + self.rect.height - RECORD_BTN_SIZE - 80:
      self._cycle_camera(-1 if mouse_pos.x < self.rect.x + self.rect.width / 2 else 1)

  def _render(self, rect: rl.Rectangle):
    self._camera.render(rect)  # fills the screen

    # camera name, top center
    gui_label(rl.Rectangle(rect.x, rect.y + 24, rect.width, 60), CAMERAS[self._cam_idx][0],
              font_size=48, font_weight=FontWeight.BOLD, alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER)

    # REC indicator (blinking), top left
    if self._params.get_bool(RECORDING_PARAM, False):
      if int(rl.get_time() * 2) % 2 == 0:
        rl.draw_circle(int(rect.x + 44), int(rect.y + 52), 16, rl.RED)
      gui_label(rl.Rectangle(rect.x + 68, rect.y + 28, 220, 48), "REC",
                font_size=40, color=rl.RED, font_weight=FontWeight.BOLD)

    # settings gear, top right
    self._gear_rect = rl.Rectangle(rect.x + rect.width - GEAR_SIZE - 24, rect.y + 24, GEAR_SIZE, GEAR_SIZE)
    rl.draw_texture_ex(self._settings_icon, rl.Vector2(self._gear_rect.x, self._gear_rect.y), 0, 1.0,
                       rl.Color(255, 255, 255, 230))

    # record button, bottom center
    bw = self._record_btn.rect.width
    self._record_btn.set_position(rect.x + (rect.width - bw) / 2, rect.y + rect.height - bw - 40)
    self._record_btn.render()


if __name__ == "__main__":
  # smoke test: shows placeholder (no camerad) + working record button / camera cycle
  gui_app.init_window("recorder")
  layout = RecorderLayout()
  for _ in gui_app.render():
    layout.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
