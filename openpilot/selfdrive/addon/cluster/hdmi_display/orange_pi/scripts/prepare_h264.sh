#!/usr/bin/env bash
# Optional dependencies: preparing a new codec must not disable JPEG reception.
set -u
python_bin=${CLUSTER_OS_PYTHON:-/usr/bin/python3}
ready() { "$python_bin" -c 'import aiortc, av, numpy, PIL, cv2' >/dev/null 2>&1; }
if ready; then
  printf '[CLUSTER_H264_DEPS] Optional receiver dependencies are available.\n'
  exit 0
fi
if command -v apt-get >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
  printf '[CLUSTER_H264_DEPS] Preparing optional H.264 dependencies; the JPEG receiver remains running during update.\n'
  timeout --kill-after=5s 120s apt-get update || true
  timeout --kill-after=5s 180s apt-get install -y --no-install-recommends \
    python3-aiortc python3-av python3-numpy python3-pil python3-opencv || true
fi
if ready; then
  printf '[CLUSTER_H264_DEPS] Optional receiver dependencies are available.\n'
else
  printf '[CLUSTER_H264_DEPS] WARNING: H.264 dependencies are unavailable. JPEG remains available; selecting H.264 will report the missing modules.\n' >&2
fi
exit 0
