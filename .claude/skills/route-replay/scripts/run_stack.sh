#!/usr/bin/env bash
# Run the FULL onroad stack against a replayed route, on the host, no device.
#
# Runs on: Linux host (native or WSL)  |  Needs: openpilot built (scons), a display for the UI.
#
# replay feeds raw CAN + sensors + calibration + camera frames; the live processes
# (card, controlsd, plannerd, selfdrived, modeld, locationd) produce carState, the plan,
# selfdriveState, the model and the EKF pose -- so the UI renders what openpilot *computes*
# off the logged route, not the logged outputs. For the passive XV40 port this is how you see
# the model path / planned trajectory / engagement state that dashcamOnly used to hide.
#
#   run_stack.sh <route> [data_dir]
#   run_stack.sh 00000030--8d74498095 /mnt/comma
#
# Stop everything: kill $(cat /tmp/op_stack.pids)

set -uo pipefail
REPO="${OPENPILOT_ROOT:-$HOME/openpilot}"
[ -d "$REPO" ] || REPO="/home/patrick/Work/openpilot"
ROUTE="${1:?usage: run_stack.sh <route> [data_dir]}"
DATA_DIR="${2:-/mnt/comma}"
LOGDIR="${LOGDIR:-/tmp/op_stack_logs}"; mkdir -p "$LOGDIR"
PIDS=/tmp/op_stack.pids; : > "$PIDS"

cd "$REPO"; source .venv/bin/activate

# platform + onboarding so the UI goes onroad instead of fingerprinting / showing terms.
# REPLAY=1 tells the processes they're driven by replayed logs, relaxing the hard realtime /
# liveness deadlines -- without it, running replay + modeld + the control processes on one host
# trips "TAKE CONTROL IMMEDIATELY" lag alerts that never happen on-device.
export FINGERPRINT=TOYOTA_CAMRY_XV40_2010 SKIP_FW_QUERY=1 REPLAY=1
python3 - <<'PY'
from openpilot.common.params import Params
from openpilot.common.version import terms_version, training_version
p = Params()
p.put("HasAcceptedTerms", terms_version)
p.put("CompletedTrainingVersion", training_version)
p.put_bool("OpenpilotEnabledToggle", True)
PY

start() {  # start <name> <cmd...>
  local name="$1"; shift
  "$@" > "$LOGDIR/$name.log" 2>&1 &
  echo $! >> "$PIDS"
  echo "  started $name (pid $!)"
}

# the live processes will own these; replay must not also publish them
BLOCK="carState,carParams,carOutput,sendcan,controlsState,carControl,longitudinalPlan,selfdriveState,onroadEvents,modelV2,cameraOdometry,drivingModelData,livePose"

echo "==> replay $ROUTE"
start replay openpilot/tools/replay/replay "$ROUTE" --data_dir "$DATA_DIR" --ecam --block "$BLOCK"
# wait until replay is actually publishing camera frames
until python3 -c "
from openpilot.cereal import messaging; import time
sm=messaging.SubMaster(['roadCameraState']); t=time.time()
while time.time()-t<15:
    sm.update(100)
    if sm.updated['roadCameraState']: raise SystemExit(0)
raise SystemExit(1)" 2>/dev/null; do echo "  waiting for replay..."; done
echo "  replay publishing"

echo "==> onroad processes"
start card       python3 openpilot/selfdrive/car/card.py
sleep 3   # let card write CarParams before the consumers read it
start modeld     python3 openpilot/selfdrive/modeld/modeld.py
start locationd  python3 openpilot/selfdrive/locationd/locationd.py
start plannerd   python3 openpilot/selfdrive/controls/plannerd.py
start controlsd  python3 openpilot/selfdrive/controls/controlsd.py
start selfdrived python3 openpilot/selfdrive/selfdrived/selfdrived.py
sleep 2
start ui         env SCALE=2 openpilot/selfdrive/ui/ui.py

echo "all up. logs in $LOGDIR, pids in $PIDS"
echo "stop with: kill \$(cat $PIDS)"

# --- watchdog: modeld's VisionIPC client hangs across replay loops (modelV2 -> 0 Hz), which
# cascades to locationd (livePose) and plannerd (longitudinalPlan) and shows as a "comm issue".
# Restart modeld+locationd whenever modelV2 stalls. Runs until this script is killed.
modelv2_alive() {
  python3 -c "
from openpilot.cereal import messaging; import time
sm=messaging.SubMaster(['modelV2']); t=time.time()
while time.time()-t<3:
    sm.update(100)
    if sm.updated['modelV2']: raise SystemExit(0)
raise SystemExit(1)" 2>/dev/null
}
echo "==> watchdog running (restarts modeld+locationd if modelV2 stalls)"
while sleep 10; do
  modelv2_alive && continue
  echo "  [watchdog] modelV2 stalled -> restarting modeld + locationd"
  pkill -f "[m]odeld/modeld"; pkill -f "[l]ocationd/locationd"; sleep 2
  python3 openpilot/selfdrive/modeld/modeld.py    > "$LOGDIR/modeld.log"    2>&1 & echo $! >> "$PIDS"
  python3 openpilot/selfdrive/locationd/locationd.py > "$LOGDIR/locationd.log" 2>&1 & echo $! >> "$PIDS"
done
