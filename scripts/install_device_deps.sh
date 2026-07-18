#!/usr/bin/env bash
# Install this fork's extra Python deps on a comma device.
#
# AGNOS ships a prebuilt venv at /usr/local/venv, but that lives on the read-only,
# A/B-swapped rootfs: an AGNOS update replaces the whole partition and takes anything
# pip-installed there with it. So install into /data/pylibs instead, which persists.
# openpilot/system/loggerd/smb_upload.py appends that dir to sys.path.
#
# usage:
#   scripts/install_device_deps.sh              # install (or repair)
#   scripts/install_device_deps.sh --if-missing # no-op when already importable
set -e

TARGET=/data/pylibs
PYTHON=${PYTHON:-/usr/local/venv/bin/python3}

# --no-deps with an explicit list: AGNOS already provides cryptography/cffi/pycparser,
# and letting pip resolve deps here pulls a NEWER cryptography that conflicts with the
# venv's pyopenssl. sspilib is win32-only, so not needed. The import check below fails
# loudly if smbprotocol ever grows a dep this list is missing.
DEPS=(smbprotocol pyspnego)

[ -x "$PYTHON" ] || PYTHON=python3

check() {
  "$PYTHON" -c "
import sys
sys.path.append('$TARGET')
import smbclient
" 2>/dev/null
}

if [ "$1" = "--if-missing" ] && check; then
  exit 0
fi

mkdir -p "$TARGET"

# The rootfs sits ~90% full, so keep pip's build/temp files on /data or it fails
# with ENOSPC part way through.
mkdir -p /data/tmp/pip
TMPDIR=/data/tmp/pip "$PYTHON" -m pip install --upgrade --no-deps --target="$TARGET" "${DEPS[@]}"

if check; then
  echo "device deps OK: ${DEPS[*]} -> $TARGET"
else
  echo "ERROR: install completed but import still fails" >&2
  exit 1
fi
