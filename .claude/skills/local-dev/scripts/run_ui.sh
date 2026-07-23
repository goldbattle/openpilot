#!/usr/bin/env bash
# Run the openpilot UI locally against a replayed route -- the whole stack, no device.
#
# Runs on: WSL  |  Needs: openpilot built (scons) + WSLg display.
#
# From the host:
#   wsl -d Ubuntu-24.04 -e bash /mnt/c/.../local-dev/scripts/run_ui.sh
# or copy it into the WSL checkout and run it there.
#
#   run_ui.sh                     # demo route, mici layout (536x240, what the comma 4 runs)
#   run_ui.sh <route-id>          # a specific route (from your comma account; needs auth.py)
#   BIG=1 run_ui.sh               # the large tici/tizi layout instead
#
# replay publishes logged messages + camera frames over VisionIPC, so the real UI renders
# exactly what it would onroad: camera feed with the model path/lane/lead overlay, the driver
# page one swipe right, and (this fork) the capture status line. deviceState.started is true in
# the demo log, so the UI goes onroad on its own.
set -euo pipefail

REPO="${OPENPILOT_ROOT:-$HOME/openpilot}"
ROUTE="${1:-}"
cd "$REPO"
source .venv/bin/activate

pkill -f "tools/replay/replay" 2>/dev/null || true
pkill -f "selfdrive/ui/ui.py" 2>/dev/null || true
sleep 1

if [ -z "$ROUTE" ]; then
  echo "==> replay --demo (headless; its console UI spews ANSI, so it goes to /dev/null)"
  setsid nohup ./openpilot/tools/replay/replay --demo >/dev/null 2>&1 </dev/null &
else
  echo "==> replay $ROUTE"
  setsid nohup ./openpilot/tools/replay/replay "$ROUTE" >/dev/null 2>&1 </dev/null &
fi

# Wait until messages are actually on the wire before opening the window -- a remote route
# has to download first, which can take a while.
python - <<'PY'
import time
from openpilot.cereal import messaging
sm = messaging.SubMaster(['carState', 'roadCameraState'])
t0 = time.time()
while time.time() - t0 < 120:
    sm.update(100)
    if sm.updated['carState'] and sm.updated['roadCameraState']:
        print(f"   replay publishing after {time.time()-t0:.1f}s")
        break
else:
    print("   WARNING: no messages after 120s -- replay may have failed to load the route")
PY

echo "==> UI. Swipe/drag left<->right to change page. Close the window to stop."
# The wifi_manager DBus errors in the log are harmless: no NetworkManager in WSL, the call is
# caught and retried in a daemon thread. If the window dies with 'XIO: fatal IO error' that is
# transient Xwayland flakiness under WSLg -- just rerun, it comes up.
python openpilot/selfdrive/ui/ui.py || echo "UI exited $?"

pkill -f "tools/replay/replay" 2>/dev/null || true
