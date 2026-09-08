"""Headless smoke test for the mici UI: build the real main layout and render every page.

Catches what py_compile and ruff cannot -- missing attributes, wrong widget signatures, a
page that throws on its first frame -- without a display or a device:

    xvfb-run -a --server-args="-screen 0 1280x800x24" python .claude/skills/local-dev/scripts/ui_smoke.py

Everything is offroad here, so the camera pages draw their placeholder rather than video;
this proves the layout and the widget wiring, not the video path."""
import pyray as rl
from openpilot.common.params import Params
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.ui_state import ui_state

gui_app.init_window("smoke")

from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.selfdrive.ui.mici.layouts.settings.recording import RecordingLayoutMici, CAMERAS

p = Params()
main = MiciMainLayout()
rec = RecordingLayoutMici()
rect = rl.Rectangle(0, 0, gui_app.width, gui_app.height)

frames = 0
for _ in gui_app.render():
  ui_state.update()
  # walk every page of the main scroller, then the recording page
  page = main._scroller._items[(frames // 6) % len(main._scroller._items)]
  page.render(rect)
  rec.render(rect)
  frames += 1
  if frames > len(main._scroller._items) * 6 + 10:
    break

# exercise the toggle path for each camera
for cam in CAMERAS:
  rec._toggle(cam)
  assert p.get("RecordCamera") == cam, p.get("RecordCamera")
  assert p.get_bool("ForceOnroad")
  rec.render(rect)
  rec._toggle(cam)
  assert not p.get_bool("ForceOnroad")
  rec.render(rect)

# _onroad_layout must follow RecordCamera
for cam, attr in (("road", "_road_onroad_layout"), ("wide", "_wide_onroad_layout"), ("driver", "_driver_onroad_layout")):
  ui_state.record_camera = cam
  assert main._onroad_layout is getattr(main, attr), cam
ui_state.record_camera = "nonsense"
assert main._onroad_layout is main._road_onroad_layout

print(f"smoke OK: {frames} frames, {len(main._scroller._items)} pages")

# the recording page must end with a live preview that follows the selection
from openpilot.selfdrive.ui.mici.layouts.settings.recording import RecordingPreview, STREAMS
assert isinstance(rec._scroller._items[-1], RecordingPreview), rec._scroller._items
for cam in CAMERAS:
  rec._toggle(cam)
  rec.render(rect)
  assert rec._preview._camera == cam
  rec._toggle(cam)
print("preview OK")
