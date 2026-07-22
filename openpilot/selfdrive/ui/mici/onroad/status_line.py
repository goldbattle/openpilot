"""recorder fork: the one-line capture status shown on both onroad pages.

Left: GPS fix accuracy, then 1s-averaged IMU magnitudes. Right: a blinking dot and the
elapsed onroad time, which is exactly how long loggerd has been writing this route.
"""
import math
import time
import pyray as rl
from collections import deque

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.widgets.label import gui_label

PAD = 12
FONT_SIZE = 22
GRAVITY = 9.81


def fmt_elapsed(secs: float) -> str:
  """h:mm:ss. Seconds are shown because h:mm alone reads a frozen "0:00" for the first
  full minute, which is indistinguishable from the recorder being broken -- the ticking
  digit is the only proof anything is happening. Hours are unbounded (no % 24)."""
  total = int(secs)
  return f"{total // 3600}:{(total // 60) % 60:02d}:{total % 60:02d}"


def onroad_elapsed_str() -> str:
  """Time since the onroad transition. ui_state sets started_time from time.monotonic() on
  the offroad->onroad edge, and loggerd starts on that same edge, so this is the route
  length. monotonic, not wall clock: the device slews its clock on GPS+NTP sync, which
  would make this leap or go negative mid-drive."""
  if not ui_state.started:
    return ""
  return fmt_elapsed(time.monotonic() - ui_state.started_time)


def _magnitude(v) -> float:
  return math.sqrt(sum(float(x) * float(x) for x in v))


class _SensorAverager:
  """Rolling 1s mean of the IMU magnitudes -- the raw values (esp. gyro) are far too noisy to read."""
  WINDOW_S = 1.0

  def __init__(self):
    self._samples: deque[tuple[float, float, float]] = deque()
    self._last_frame = -1

  def update(self) -> None:
    # sensor_status() is called by both onroad pages, which are both live during a swipe;
    # sample only once per frame so the window stays 1s of real time either way
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


def render(rect: rl.Rectangle) -> None:
  """Draw along the bottom of `rect`. Bottom rather than top because both pages already
  contend for the top corners -- the set-speed bubble and dmoji on the road view, the eye
  icons and awareness readout on the driver view."""
  y = rect.y + rect.height - FONT_SIZE - 6
  gui_label(rl.Rectangle(rect.x + PAD, y, rect.width - 2 * PAD, FONT_SIZE + 2), sensor_status(),
            font_size=FONT_SIZE, font_weight=FontWeight.BOLD, color=rl.Color(255, 255, 255, 235))

  elapsed = onroad_elapsed_str()
  if not elapsed:
    return
  # blinking dot: the only element that proves the UI is still updating, not frozen on a
  # stale frame. 2 Hz, same as a camcorder.
  if int(rl.get_time() * 2) % 2 == 0:
    rl.draw_circle(int(rect.x + rect.width - PAD - 110), int(y + FONT_SIZE / 2), 6, rl.RED)
  gui_label(rl.Rectangle(rect.x + rect.width - PAD - 96, y, 96, FONT_SIZE + 2), f"REC {elapsed}",
            font_size=FONT_SIZE, font_weight=FontWeight.BOLD, color=rl.RED)


def _self_check() -> None:
  assert fmt_elapsed(0) == "0:00:00", fmt_elapsed(0)
  assert fmt_elapsed(59.9) == "0:00:59", fmt_elapsed(59.9)
  assert fmt_elapsed(60) == "0:01:00", fmt_elapsed(60)
  assert fmt_elapsed(3661) == "1:01:01", fmt_elapsed(3661)
  # a long parked capture must not wrap at 24h
  assert fmt_elapsed(30 * 3600) == "30:00:00", fmt_elapsed(30 * 3600)
  print("status_line self-check OK")


if __name__ == "__main__":
  _self_check()
