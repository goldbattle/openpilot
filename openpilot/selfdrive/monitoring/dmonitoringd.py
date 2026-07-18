#!/usr/bin/env python3
import openpilot.cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process
from openpilot.selfdrive.monitoring.policy import DriverMonitoring


def dmonitoringd_thread():
  config_realtime_process([0, 1, 2, 3], 5)

  params = Params()
  pm = messaging.PubMaster(['driverMonitoringState'])
  sm = messaging.SubMaster(['driverStateV2', 'liveCalibration', 'carState', 'selfdriveState', 'modelV2'], poll='driverStateV2')

  DM = DriverMonitoring(rhd_saved=params.get_bool("IsRhdDetected"), always_on=params.get_bool("AlwaysOnDM"))
  # recorder fork: always the car-less path. all_checks() below can never pass here --
  # it covers carState/modelV2/liveCalibration/selfdriveState, all only_onroad in this
  # fork, so never alive. Without this we'd publish an all-defaults driverMonitoringState
  # (faceDetected=False) forever and the UI's dmoji would never track a face.
  # Not driven by IsDriverViewEnabled: onboarding registers an inactivity callback that
  # clears that param on every screen timeout, and setting it would also pull in soundd
  # and hardwared's not_driver_view startup condition.
  demo_mode = True

  # 20Hz <- dmonitoringmodeld
  while True:
    sm.update()
    if not sm.updated['driverStateV2']:
      # iterate when model has new output
      continue

    valid = sm.all_checks()
    if demo_mode and sm.valid['driverStateV2']:
      DM.run_step(sm, demo=True)
    elif valid:
      DM.run_step(sm, demo=demo_mode)

    # publish
    dat = DM.get_state_packet(valid=valid)
    pm.send('driverMonitoringState', dat)

    # load live always-on toggle
    if sm['driverStateV2'].frameId % 40 == 1:
      DM.always_on = params.get_bool("AlwaysOnDM")
      # recorder fork: demo_mode is pinned True above, so the IsDriverViewEnabled re-read
      # that normally lived here is dropped -- it would just clear it again every 2s.

    # save rhd virtual toggle every 5 mins
    if (sm['driverStateV2'].frameId % 6000 == 0 and not demo_mode and
     DM.wheelpos_offsetter.filtered_stat.n > DM.settings._WHEELPOS_FILTER_MIN_COUNT and
     DM.wheel_on_right == (DM.wheelpos_offsetter.filtered_stat.M > DM.settings._WHEELPOS_THRESHOLD)):
      params.put_bool("IsRhdDetected", DM.wheel_on_right)

def main():
  dmonitoringd_thread()


if __name__ == '__main__':
  main()
