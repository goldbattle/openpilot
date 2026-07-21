import os
import operator
import platform

from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.common.hardware import PC, TICI
from openpilot.system.manager.process import PythonProcess, NativeProcess, DaemonProcess

WEBCAM = os.getenv("USE_WEBCAM") is not None

def driverview(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started or params.get_bool("IsDriverViewEnabled")

def notcar(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and CP.notCar

def iscar(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not CP.notCar

def logging(started: bool, params: Params, CP: car.CarParams) -> bool:
  run = (not CP.notCar) or not params.get_bool("DisableLogging")
  return started and run

def ublox_available() -> bool:
  return os.path.exists('/dev/ttyHS0') and not os.path.exists('/persist/comma/use-quectel-gps')

def ublox(started: bool, params: Params, CP: car.CarParams) -> bool:
  use_ublox = ublox_available()
  if use_ublox != params.get_bool("UbloxAvailable"):
    params.put_bool("UbloxAvailable", use_ublox, block=True)
  # recorder fork: also while manually recording, so a car-off capture still gets a fix
  return (started or recording(started, params, CP)) and use_ublox

def joystick(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and params.get_bool("JoystickDebugMode")

def not_joystick(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not params.get_bool("JoystickDebugMode")

def long_maneuver(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and params.get_bool("LongitudinalManeuverMode")

def lat_maneuver(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and params.get_bool("LateralManeuverMode")

def not_long_maneuver(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not params.get_bool("LongitudinalManeuverMode")

def qcomgps(started: bool, params: Params, CP: car.CarParams) -> bool:
  # recorder fork: also while manually recording, so a car-off capture still gets a fix
  return (started or recording(started, params, CP)) and not ublox_available()

def always_run(started: bool, params: Params, CP: car.CarParams) -> bool:
  return True

def only_onroad(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started

def only_offroad(started: bool, params: Params, CP: car.CarParams) -> bool:
  return not started

def livestream(started: bool, params: Params, CP: car.CarParams) -> bool:
  return params.get_bool("IsLiveStreaming")

def recording(started: bool, params: Params, CP: car.CarParams) -> bool:
  # data-recorder fork: the record path runs off this param, independent of car/ignition/CAN.
  # Same mechanism as livestream/joystick/driverview above — manager re-reads it every loop.
  return params.get_bool("Recording")

# n-ary: operator.or_/and_ are strictly binary, so these used to blow up with
# "expected 2 arguments" as soon as a third predicate was combined. The list
# comprehensions are deliberate -- any()/all() on a generator would short-circuit and
# skip later predicates, and some of these have side effects (ublox writes a param).
def or_(*fns):
  return lambda *args: any([fn(*args) for fn in fns])

def and_(*fns):
  return lambda *args: all([fn(*args) for fn in fns])

def not_(*fns):
  return lambda *args: operator.not_(*(fn(*args) for fn in fns))

procs = [
  # recorder fork: never phone home to comma (no athena tunnel, no uploads)
  DaemonProcess("manage_athenad", "openpilot.system.athena.manage_athenad", "AthenadPid", enabled=False),

  NativeProcess("loggerd", "openpilot/system/loggerd", ["./loggerd"], or_(logging, recording)),
  NativeProcess("encoderd", "openpilot/system/loggerd", ["./encoderd"], or_(only_onroad, recording)),
  NativeProcess("stream_encoderd", "openpilot/system/loggerd", ["./encoderd", "--stream"], or_(and_(livestream, not_(iscar)), notcar)),
  PythonProcess("logmessaged", "openpilot.system.logmessaged", always_run),

  # recorder fork: + recording, so the manual (car-off) recorder has camera feeds to show and log
  NativeProcess("camerad", "openpilot/system/camerad", ["./camerad"], or_(driverview, livestream, recording), enabled=not WEBCAM),
  PythonProcess("webcamerad", "openpilot.system.camerad.webcam.camerad", driverview, enabled=WEBCAM),
  PythonProcess("proclogd", "openpilot.system.proclogd", only_onroad, enabled=platform.system() != "Darwin"),
  PythonProcess("journald", "openpilot.system.journald", only_onroad, platform.system() != "Darwin"),
  PythonProcess("micd", "openpilot.system.micd", iscar),
  PythonProcess("timed", "openpilot.system.timed", always_run, enabled=not PC),

  PythonProcess("modeld", "openpilot.selfdrive.modeld.modeld", only_onroad),
  # recorder fork: + recording, so the driver page's dmoji has live pose data while manually recording
  PythonProcess("dmonitoringmodeld", "openpilot.selfdrive.modeld.dmonitoringmodeld", or_(driverview, recording), enabled=(WEBCAM or not PC)),

  # recorder fork: + recording, so a car-off capture still logs the IMU
  PythonProcess("sensord", "openpilot.system.sensord.sensord", or_(only_onroad, recording), enabled=not PC),
  PythonProcess("ui", "openpilot.selfdrive.ui.ui", always_run, restart_if_crash=True),
  PythonProcess("soundd", "openpilot.selfdrive.ui.soundd", driverview),
  PythonProcess("locationd", "openpilot.selfdrive.locationd.locationd", only_onroad),
  NativeProcess("_pandad", "openpilot/selfdrive/pandad", ["./pandad"], always_run, enabled=False),
  PythonProcess("calibrationd", "openpilot.selfdrive.locationd.calibrationd", only_onroad),
  PythonProcess("torqued", "openpilot.selfdrive.locationd.torqued", only_onroad),
  PythonProcess("controlsd", "openpilot.selfdrive.controls.controlsd", and_(not_joystick, iscar)),
  PythonProcess("joystickd", "openpilot.tools.joystick.joystickd", or_(joystick, notcar)),
  PythonProcess("selfdrived", "openpilot.selfdrive.selfdrived.selfdrived", only_onroad),
  PythonProcess("card", "openpilot.selfdrive.car.card", only_onroad),
  PythonProcess("deleter", "openpilot.system.loggerd.deleter", always_run),
  PythonProcess("dmonitoringd", "openpilot.selfdrive.monitoring.dmonitoringd", or_(driverview, recording), enabled=(WEBCAM or not PC)),
  PythonProcess("qcomgpsd", "openpilot.system.qcomgpsd.qcomgpsd", qcomgps, enabled=TICI),
  PythonProcess("pandad", "openpilot.selfdrive.pandad.pandad", always_run),
  PythonProcess("paramsd", "openpilot.selfdrive.locationd.paramsd", only_onroad),
  PythonProcess("lagd", "openpilot.selfdrive.locationd.lagd", only_onroad),
  PythonProcess("ubloxd", "openpilot.system.ubloxd.ubloxd", ublox, enabled=TICI),
  PythonProcess("pigeond", "openpilot.system.ubloxd.pigeond", ublox, enabled=TICI),
  PythonProcess("plannerd", "openpilot.selfdrive.controls.plannerd", not_long_maneuver),
  PythonProcess("maneuversd", "openpilot.tools.longitudinal_maneuvers.maneuversd", long_maneuver),
  PythonProcess("lateral_maneuversd", "openpilot.tools.lateral_maneuvers.lateral_maneuversd", lat_maneuver),
  PythonProcess("radard", "openpilot.selfdrive.controls.radard", only_onroad),
  PythonProcess("hardwared", "openpilot.system.hardware.hardwared", always_run),
  PythonProcess("modem", "openpilot.common.hardware.tici.modem", always_run, enabled=TICI),
  PythonProcess("tombstoned", "openpilot.system.tombstoned", always_run, enabled=not PC),
  # recorder fork: enabled. It tracks whatever remote+branch /data/openpilot is checked out on
  # (this fork's master), not commaai's, so "check for update" pulls our own pushed commits.
  # NOTE: it finalizes an OverlayFS copy that gets swapped over /data/openpilot on launch, so
  # uncommitted on-device edits get reverted -- push to the fork to make a change stick.
  PythonProcess("updated", "openpilot.system.updated.updated", only_offroad, enabled=not PC),
  # recorder fork: disabled — data stays on device, never uploaded to comma
  PythonProcess("uploader", "openpilot.system.loggerd.uploader", always_run, enabled=False),
  PythonProcess("feedbackd", "openpilot.selfdrive.ui.feedback.feedbackd", only_onroad),

  # debug procs
  NativeProcess("bridge", "openpilot/cereal/messaging", ["./bridge"], notcar),
  PythonProcess("webrtcd", "openpilot.system.webrtc.webrtcd", or_(and_(livestream, not_(iscar)), notcar)),
  PythonProcess("joystick", "openpilot.tools.joystick.joystick_control", and_(joystick, iscar)),
]

managed_processes = {p.name: p for p in procs}
