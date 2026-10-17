#!/usr/bin/env bash
# Prepare mandatory dependencies before replacing or stopping the live receiver.
set -euo pipefail
python_bin=${CLUSTER_OS_PYTHON:-/usr/bin/python3}
ready() { "$python_bin" -c 'import aiortc, av, numpy, PIL, cv2' >/dev/null 2>&1; }
if ready; then
  printf '[CLUSTER_H264_DEPS] Receiver dependencies are available.\n'
  exit 0
fi
if command -v apt-get >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
  printf '[CLUSTER_H264_DEPS] Preparing required H.264 dependencies before receiver replacement.\n'
  timeout --kill-after=5s 120s apt-get update || true
  timeout --kill-after=5s 180s apt-get install -y --no-install-recommends \
    python3-aiortc python3-av python3-numpy python3-pil python3-opencv || true
fi
if ready; then
  printf '[CLUSTER_H264_DEPS] Receiver dependencies are available.\n'
else
  printf '[CLUSTER_H264_DEPS] ERROR: Required H.264 dependencies are unavailable; receiver replacement cancelled.\n' >&2
  exit 1
fi
exit 0
