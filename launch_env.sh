#!/usr/bin/env bash

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

# models get lower priority than ui
# - ui is ~5ms
# - modeld is 20ms
# - DM is 10ms
# in order to run ui at 60fps (16.67ms), we need to allow
# it to preempt the model workloads. we have enough
# headroom for this until ui is moved to the CPU.
export QCOM_PRIORITY=12

if [ -z "$AGNOS_VERSION" ]; then
  export AGNOS_VERSION="18.5"
fi

# recorder fork: this device is permanently installed in one car, a 2010 Camry (XV40),
# which cannot be fingerprinted -- it predates the FW/VIN query and none of its CAN
# addresses match a supported platform. Pin the platform here so it survives a reboot.
# SKIP_FW_QUERY also avoids the ECU probe, which would drive the OBD multiplexer that
# pandad holds on for passive logging. Guarded so scripts/launch_corolla.sh, which
# exports FINGERPRINT before calling launch_openpilot.sh, still wins.
if [ -z "$FINGERPRINT" ]; then
  export FINGERPRINT="TOYOTA_CAMRY_XV40_2010"
  export SKIP_FW_QUERY="1"
fi

export STAGING_ROOT="/data/safe_staging"
